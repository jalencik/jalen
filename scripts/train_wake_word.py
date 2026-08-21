"""
Train a real "Hey Jalen" wake word.

    python scripts\\train_wake_word.py generate   # make the audio (slow, resumable)
    python scripts\\train_wake_word.py train      # fit the head, export the ONNX
    python scripts\\train_wake_word.py evaluate   # false-accept / false-reject rates
    python scripts\\train_wake_word.py all        # all three

WHY THIS EXISTS. openWakeWord ships pretrained models for "hey jarvis",
"alexa", "hey mycroft" and a handful of others. There is no "hey jalen" and
there never will be, so the choice is train one or call him by another
name.

WHAT IS ACTUALLY BEING TRAINED — and it is much smaller than it sounds.
openWakeWord is three models in a row, and only the last is specific to a
phrase:

    audio -> melspectrogram.onnx -> embedding_model.onnx -> [head] -> score
             \\____________ frozen, already in models/ ____________/

The first two are generic speech feature extractors, shipped with the
package and already sitting on this disk. Only the head — 1536 inputs, two
small hidden layers, one output — knows what "Hey Jalen" sounds like. That
is a few hundred thousand parameters, trainable on a CPU in under a minute,
which is why this needs no GPU and no PyTorch.

WHERE THE DATA COMES FROM. edge-tts is already a dependency, for Jalen's
voice, and it offers hundreds of English voices at controllable rate and
pitch. Sweeping voice x rate x pitch produces thousands of distinct
utterances of "Hey Jalen" for free, with no API key and no dataset licence.
Augmentation — background noise, gain, time shift — stops the model learning
"clean studio speech" as part of the phrase.

WHY THE NEGATIVES MATTER MORE THAN THE POSITIVES. A model trained on "Hey
Jalen" versus silence learns to fire on any speech at all, and then it
triggers on the television all evening. What teaches it the actual boundary
is CONFUSABLE negatives: "hey Alan", "hey Helen", "hey Jason", "hey Galen",
"Jalen" with no "hey", and — importantly — "hey Jarvis", the name it is
replacing. Those are the sounds it must learn to reject.

SHIPPING RULE. A wake word that fires on the television is worse than the
old wake word. `evaluate` prints a false-accept and a false-reject rate on
held-out audio the model never saw. No numbers, no swap.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data" / "wake_training"
POS_DIR = DATA_DIR / "positive"
NEG_DIR = DATA_DIR / "negative"

SAMPLE_RATE = 16000
# 0.76s embedding window + 15 hops of 0.08s = 1.96s, which is what produces
# the 16 x 96 feature block the pretrained models are built around. Anything
# else and the exported head will not fit openWakeWord's runtime.
CLIP_SECONDS = 2.0
CLIP_SAMPLES = int(SAMPLE_RATE * CLIP_SECONDS)
EMBEDDING_SHAPE = (16, 96)
N_FEATURES = EMBEDDING_SHAPE[0] * EMBEDDING_SHAPE[1]

WAKE_PHRASE = "Hey Jalen"

# Sounds close enough to "Hey Jalen" that the model must be shown the
# difference explicitly. Without these it learns "someone is speaking".
CONFUSABLES = [
    "Hey Alan", "Hey Helen", "Hey Galen", "Hey Jason", "Hey Jaylen",
    "Hey Dylan", "Hey Elena", "Hey Kaylen", "Hey Jane", "Hey Jarvis",
    "Jalen", "Jalen Rose", "Hey there", "Hey you", "Hey",
    "A Jalen", "Say Jalen", "Hey Jill", "Hey Jenna", "Hey Alien",
]

# Ordinary speech, so the model is not merely a "is this a "hey X" phrase"
# detector. These are the sentences a television or a conversation supplies.
BACKGROUND_SPEECH = [
    "what time is it", "open the browser please", "I'll be there in a minute",
    "the weather tomorrow looks fine", "can you send me that file",
    "let me know when you're free", "that's not what I meant",
    "we should probably head out now", "turn the volume down a bit",
    "I think the meeting is at three",
]


# --------------------------------------------------------------- audio I/O
def write_wav(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(pcm.astype(np.int16).tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as fh:
        raw = fh.readframes(fh.getnframes())
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if fh.getframerate() != SAMPLE_RATE:
            # Linear resample. Good enough: these are training clips, and the
            # feature extractor's own mel filterbank dominates any error here.
            duration = len(pcm) / fh.getframerate()
            target = int(duration * SAMPLE_RATE)
            pcm = np.interp(
                np.linspace(0, len(pcm), target, endpoint=False),
                np.arange(len(pcm)),
                pcm,
            )
    return pcm


def fit_to_clip(pcm: np.ndarray, offset: float | None = None) -> np.ndarray:
    """
    Centre (or randomly place) the utterance inside a fixed 2-second clip.

    Random placement is not cosmetic. Every positive sitting at exactly the
    same offset teaches the model that the phrase begins 400ms in, and it
    then misses you whenever you speak slightly early or late.
    """
    out = np.zeros(CLIP_SAMPLES, dtype=np.float32)
    pcm = pcm[:CLIP_SAMPLES]
    room = CLIP_SAMPLES - len(pcm)
    start = int(room * (random.random() if offset is None else offset))
    out[start:start + len(pcm)] = pcm
    return out


def augment(pcm: np.ndarray, rng: random.Random) -> np.ndarray:
    """Noise, gain and placement, so the phrase is not learned as studio-clean."""
    out = fit_to_clip(pcm, offset=rng.random())
    out = out * rng.uniform(0.35, 1.25)                      # distance from the mic
    noise_level = rng.uniform(0.0, 0.04) * (np.abs(out).max() or 1000.0)
    out = out + np.random.randn(CLIP_SAMPLES).astype(np.float32) * noise_level
    return np.clip(out, -32768, 32767)


# ------------------------------------------------------------- generation
async def _english_voices(limit: int) -> list[str]:
    import edge_tts

    voices = await edge_tts.list_voices()
    names = [v["ShortName"] for v in voices if v["ShortName"].startswith("en-")]
    names.sort()
    return names[:limit]


async def _say(text: str, voice: str, rate: str, pitch: str, path: Path) -> bool:
    """
    Render one utterance to a wav. Returns False on failure.

    edge-tts is a network service and this makes thousands of calls, so
    individual failures are expected and must not abort a generation run
    that may already be an hour in. The file is simply missing and the next
    resumable run picks it up.
    """
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        mp3 = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        if not mp3:
            return False
        pcm = _decode_mp3(mp3)
        if pcm is None or len(pcm) < SAMPLE_RATE // 4:
            return False
        write_wav(path, fit_to_clip(pcm, offset=0.35))
        return True
    except Exception:
        return False


def _decode_mp3(data: bytes) -> np.ndarray | None:
    """
    MP3 -> 16 kHz mono float samples.

    Delegates to Speaker._decode_mp3 rather than reimplementing it. Jalen
    already decodes edge-tts mp3 every time he speaks, using PyAV (which
    arrives with faster-whisper, so no system ffmpeg is needed). A second
    decoder here would be a second thing to keep working, and the two would
    drift — this pipeline would start training on audio that does not sound
    like the audio the assistant actually produces.
    """
    from jarvis.audio.tts import Speaker

    try:
        pcm, rate = Speaker._decode_mp3(data)
    except Exception:
        return None
    if pcm is None or len(pcm) == 0:
        return None

    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    # Speaker works in float; the feature extractor wants int16-scaled values.
    if np.abs(pcm).max() <= 1.5:
        pcm = pcm * 32767.0
    if rate != SAMPLE_RATE:
        target = int(len(pcm) / rate * SAMPLE_RATE)
        pcm = np.interp(
            np.linspace(0, len(pcm), target, endpoint=False),
            np.arange(len(pcm)),
            pcm,
        ).astype(np.float32)
    return pcm


async def generate(voices_limit: int = 40) -> None:
    """
    Render every (phrase, voice, rate, pitch) combination that is missing.

    Resumable by design: a clip whose file already exists is skipped. These
    runs take a long time and depend on a network service, and starting over
    from zero after an interruption is how a training pipeline gets
    abandoned.
    """
    voices = await _english_voices(voices_limit)
    if not voices:
        print("Could not list any English voices — is the network up?")
        return
    print(f"{len(voices)} voices")

    rates = ["-15%", "+0%", "+15%", "+30%"]
    pitches = ["-25Hz", "+0Hz", "+25Hz"]

    jobs: list[tuple[str, str, str, str, Path]] = []
    for voice in voices:
        for rate in rates:
            for pitch in pitches:
                stem = f"{voice}_{rate}_{pitch}".replace("%", "p").replace("+", "")
                jobs.append((WAKE_PHRASE, voice, rate, pitch, POS_DIR / f"{stem}.wav"))

    negatives = CONFUSABLES + BACKGROUND_SPEECH
    for voice in voices:
        for index, phrase in enumerate(negatives):
            rate = rates[index % len(rates)]
            pitch = pitches[index % len(pitches)]
            stem = f"{voice}_{index:02d}".replace("%", "p").replace("+", "")
            jobs.append((phrase, voice, rate, pitch, NEG_DIR / f"{stem}.wav"))

    todo = [job for job in jobs if not job[4].exists()]
    print(f"{len(jobs)} clips wanted, {len(jobs) - len(todo)} already on disk, "
          f"{len(todo)} to render")

    done = failed = 0
    # Six at a time: enough to hide the round-trip, gentle enough not to get
    # throttled and start failing every request.
    semaphore = asyncio.Semaphore(6)

    async def worker(job):
        nonlocal done, failed
        text, voice, rate, pitch, path = job
        async with semaphore:
            ok = await _say(text, voice, rate, pitch, path)
        if ok:
            done += 1
        else:
            failed += 1
        if (done + failed) % 50 == 0:
            print(f"  {done} rendered, {failed} failed")

    await asyncio.gather(*(worker(job) for job in todo))
    print(f"done: {done} rendered, {failed} failed")
    print(f"positives on disk: {len(list(POS_DIR.glob('*.wav')))}")
    print(f"negatives on disk: {len(list(NEG_DIR.glob('*.wav')))}")


# ---------------------------------------------------------------- features
def _audio_features():
    from openwakeword.utils import AudioFeatures

    return AudioFeatures(
        melspec_model_path=str(MODELS_DIR / "melspectrogram.onnx"),
        embedding_model_path=str(MODELS_DIR / "embedding_model.onnx"),
        inference_framework="onnx",
    )


def build_dataset(augmentations: int = 4, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Every clip on disk, plus augmented copies, as (features, labels)."""
    rng = random.Random(seed)
    np.random.seed(seed)

    clips: list[np.ndarray] = []
    labels: list[int] = []
    for directory, label in ((POS_DIR, 1), (NEG_DIR, 0)):
        for path in sorted(directory.glob("*.wav")):
            pcm = read_wav(path)
            clips.append(fit_to_clip(pcm, offset=0.35))
            labels.append(label)
            for _ in range(augmentations):
                clips.append(augment(pcm, rng))
                labels.append(label)

    # Pure noise and near-silence as negatives. Without them the model has
    # never seen "nothing is happening" and scores it unpredictably — which
    # is a wake word that fires at 3am.
    for _ in range(max(200, len(clips) // 20)):
        level = rng.uniform(1.0, 600.0)
        clips.append((np.random.randn(CLIP_SAMPLES) * level).astype(np.float32))
        labels.append(0)

    if not clips:
        raise SystemExit("No training clips. Run: python scripts\\train_wake_word.py generate")

    print(f"{len(clips)} clips ({sum(labels)} positive) -> embedding")
    features = _audio_features().embed_clips(
        np.array(clips, dtype=np.int16), batch_size=64
    )
    features = features.reshape(len(clips), -1)
    if features.shape[1] != N_FEATURES:
        raise SystemExit(
            f"Feature width {features.shape[1]} != {N_FEATURES}. CLIP_SECONDS is wrong."
        )
    return features.astype(np.float32), np.array(labels, dtype=np.float32)


# ---------------------------------------------------------------- training
def train_head(x: np.ndarray, y: np.ndarray, *, epochs: int = 60,
               hidden: int = 128, seed: int = 0) -> tuple[list[np.ndarray], dict]:
    """
    Fit the classification head with plain numpy and Adam.

    Deliberately no PyTorch. The whole model is 1536 -> 128 -> 128 -> 1;
    pulling in a 2.5GB dependency to fit it would make this pipeline
    something nobody can run on a fresh machine, which defeats the point of
    a wake word you can retrain whenever you rename the assistant.
    """
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(x))
    x, y = x[order], y[order]

    split = int(len(x) * 0.85)
    x_train, y_train = x[:split], y[:split]
    x_val, y_val = x[split:], y[split:]

    # Normalise on the TRAINING set only. Using the whole set leaks the
    # validation distribution into the model and flatters every number
    # printed afterwards.
    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0) + 1e-6
    x_train = (x_train - mean) / std
    x_val = (x_val - mean) / std

    sizes = [x.shape[1], hidden, hidden, 1]
    weights = [
        rng.normal(0, math.sqrt(2.0 / sizes[i]), (sizes[i], sizes[i + 1])).astype(np.float32)
        for i in range(3)
    ]
    biases = [np.zeros(sizes[i + 1], dtype=np.float32) for i in range(3)]
    params = weights + biases
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]

    # Positives are outnumbered, and an unweighted loss is minimised nicely
    # by a model that says "no" to everything — 100% accurate on negatives,
    # completely deaf.
    pos_weight = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))

    def forward(xb):
        h1 = np.maximum(0, xb @ weights[0] + biases[0])
        h2 = np.maximum(0, h1 @ weights[1] + biases[1])
        logit = (h2 @ weights[2] + biases[2]).ravel()
        return h1, h2, logit

    batch, step = 256, 0
    for epoch in range(epochs):
        idx = rng.permutation(len(x_train))
        for start in range(0, len(idx), batch):
            sel = idx[start:start + batch]
            xb, yb = x_train[sel], y_train[sel]
            h1, h2, logit = forward(xb)
            prob = 1.0 / (1.0 + np.exp(-logit))

            weight = np.where(yb == 1, pos_weight, 1.0).astype(np.float32)
            d_logit = ((prob - yb) * weight / len(sel)).astype(np.float32)

            g_w2 = h2.T @ d_logit[:, None]
            g_b2 = d_logit.sum(keepdims=True)
            d_h2 = np.outer(d_logit, weights[2].ravel()) * (h2 > 0)
            g_w1 = h1.T @ d_h2
            g_b1 = d_h2.sum(axis=0)
            d_h1 = (d_h2 @ weights[1].T) * (h1 > 0)
            g_w0 = xb.T @ d_h1
            g_b0 = d_h1.sum(axis=0)

            step += 1
            for i, grad in enumerate([g_w0, g_w1, g_w2, g_b0, g_b1, g_b2]):
                grad = grad.reshape(params[i].shape).astype(np.float32)
                m[i] = 0.9 * m[i] + 0.1 * grad
                v[i] = 0.999 * v[i] + 0.001 * (grad ** 2)
                m_hat = m[i] / (1 - 0.9 ** step)
                v_hat = v[i] / (1 - 0.999 ** step)
                params[i] -= (1e-3 * m_hat / (np.sqrt(v_hat) + 1e-8)).astype(np.float32)

        if epoch % 10 == 0 or epoch == epochs - 1:
            _, _, logit = forward(x_val)
            prob = 1.0 / (1.0 + np.exp(-logit))
            acc = ((prob > 0.5) == (y_val > 0.5)).mean()
            print(f"  epoch {epoch:3d}  val accuracy {acc:.3f}")

    stats = {"mean": mean, "std": std, "x_val": x_val, "y_val": y_val}
    return params, stats


def export_onnx(params: list[np.ndarray], mean: np.ndarray, std: np.ndarray,
                path: Path) -> None:
    """
    Write a model openWakeWord can load unchanged: [1, 16, 96] in, [1, 1] out.

    Normalisation is baked into the graph as a Sub and a Div. It has to be —
    at runtime openWakeWord hands the head raw embeddings, so a model that
    expected normalised input would silently score everything wrong with no
    error anywhere.
    """
    from onnx import TensorProto, helper, numpy_helper

    w0, w1, w2, b0, b1, b2 = params
    init = [
        numpy_helper.from_array(mean.astype(np.float32), "mean"),
        numpy_helper.from_array(std.astype(np.float32), "std"),
        numpy_helper.from_array(w0, "w0"), numpy_helper.from_array(b0, "b0"),
        numpy_helper.from_array(w1, "w1"), numpy_helper.from_array(b1, "b1"),
        numpy_helper.from_array(w2, "w2"), numpy_helper.from_array(b2, "b2"),
        numpy_helper.from_array(np.array([-1, N_FEATURES], dtype=np.int64), "flat_shape"),
    ]
    nodes = [
        helper.make_node("Reshape", ["input", "flat_shape"], ["flat"]),
        helper.make_node("Sub", ["flat", "mean"], ["centred"]),
        helper.make_node("Div", ["centred", "std"], ["scaled"]),
        helper.make_node("MatMul", ["scaled", "w0"], ["z0"]),
        helper.make_node("Add", ["z0", "b0"], ["a0"]),
        helper.make_node("Relu", ["a0"], ["h0"]),
        helper.make_node("MatMul", ["h0", "w1"], ["z1"]),
        helper.make_node("Add", ["z1", "b1"], ["a1"]),
        helper.make_node("Relu", ["a1"], ["h1"]),
        helper.make_node("MatMul", ["h1", "w2"], ["z2"]),
        helper.make_node("Add", ["z2", "b2"], ["a2"]),
        helper.make_node("Sigmoid", ["a2"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes,
        "hey_jalen",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, *EMBEDDING_SHAPE])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])],
        initializer=init,
    )
    model = helper.make_model(
        graph, producer_name="jalen",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    model.ir_version = 8      # what onnxruntime 1.29 accepts from opset 13
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(model.SerializeToString())
    print(f"wrote {path}")


def evaluate(model_path: Path, x_val: np.ndarray, y_val: np.ndarray,
             mean: np.ndarray, std: np.ndarray, threshold: float = 0.55) -> dict:
    """
    False-accept and false-reject on audio the model never saw.

    These two numbers are the ship gate. A wake word that fires on the
    television is worse than an old wake word, and "it seemed to work when I
    tried it" is not a measurement.
    """
    import onnxruntime as ort

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name

    raw = x_val * std + mean          # undo training normalisation: the graph does it
    scores = np.array([
        session.run(None, {name: row.reshape(1, *EMBEDDING_SHAPE).astype(np.float32)})[0][0][0]
        for row in raw
    ])

    positives, negatives = scores[y_val == 1], scores[y_val == 0]
    result = {
        "threshold": threshold,
        "false_reject_rate": float((positives < threshold).mean()) if len(positives) else None,
        "false_accept_rate": float((negatives >= threshold).mean()) if len(negatives) else None,
        "n_positive": int(len(positives)),
        "n_negative": int(len(negatives)),
    }
    print(json.dumps(result, indent=2))
    if result["false_accept_rate"] and result["false_accept_rate"] > 0.01:
        print("\nDO NOT SHIP: it accepts more than 1% of non-wake audio. "
              "It will fire on the television.")
    elif result["false_reject_rate"] and result["false_reject_rate"] > 0.10:
        print("\nDO NOT SHIP: it misses more than 10% of real wake words.")
    else:
        print("\nGood enough to swap in. Set wake.model to 'hey_jalen' and "
              "identity.wake_word to 'hey jalen' in config/jarvis.yaml.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a 'Hey Jalen' wake word")
    parser.add_argument("stage", choices=["generate", "train", "evaluate", "all"])
    parser.add_argument("--voices", type=int, default=40)
    parser.add_argument("--epochs", type=int, default=60)
    args = parser.parse_args()

    if args.stage in ("generate", "all"):
        asyncio.run(generate(args.voices))
    if args.stage in ("train", "evaluate", "all"):
        x, y = build_dataset()
        params, stats = train_head(x, y, epochs=args.epochs)
        out = MODELS_DIR / "hey_jalen.onnx"
        export_onnx(params, stats["mean"], stats["std"], out)
        evaluate(out, stats["x_val"], stats["y_val"], stats["mean"], stats["std"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
