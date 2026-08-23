"""
Fetch the local models Jalen needs. Run once after install:

    python scripts/download_models.py

Downloads about 8 MB total:
  - hey_jarvis_v0.1.onnx  (wake word, 1.3 MB)  + its two shared feature models
  - silero_vad.onnx       (voice activity, 2.3 MB)
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

SILERO_WHEEL = "https://files.pythonhosted.org/packages/py3/s/silero-vad/silero_vad-6.2.1-py3-none-any.whl"


def download_wake_models() -> bool:
    print("[1/2] Wake word models (openWakeWord)…")
    try:
        import openwakeword.utils as utils
    except ImportError:
        print("      openwakeword not installed. Run: pip install -r requirements.txt")
        return False
    try:
        # Pulls hey_jarvis_v0.1 plus the shared melspectrogram + embedding models.
        # openwakeword defaults target_directory to its OWN package resources dir
        # (site-packages/openwakeword/resources/models) — Model() always reads the
        # melspectrogram/embedding feature models from there regardless, so that
        # copy is required either way. But wake.py looks for the wake-word model
        # itself in THIS project's models/ first, so we also fetch a copy there:
        # explicit, visible in `models/`, and matches what this script promises.
        utils.download_models(model_names=["hey_jarvis_v0.1"])
        utils.download_models(model_names=["hey_jarvis_v0.1"], target_directory=str(MODELS))
        print("      done.")
        return True
    except Exception as exc:
        print(f"      failed: {exc}")
        print("      Manual fallback: download hey_jarvis_v0.1.onnx from")
        print("      https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1")
        print(f"      and put it in {MODELS}")
        return False


def download_silero() -> bool:
    """
    We take the .onnx out of the wheel rather than pip-installing silero-vad,
    because that package hard-depends on torch + torchaudio (multiple GB).
    """
    target = MODELS / "silero_vad.onnx"
    if target.exists():
        print("[2/2] silero_vad.onnx already present.")
        return True
    print("[2/2] Silero VAD (extracting ONNX from the wheel, skipping torch)…")
    MODELS.mkdir(exist_ok=True)
    try:
        with urlopen(SILERO_WHEEL, timeout=90) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next(
                n for n in zf.namelist()
                if n.endswith("silero_vad.onnx") and "16k" not in n and "half" not in n
            )
            target.write_bytes(zf.read(name))
        print(f"      done — {target.stat().st_size / 1e6:.1f} MB")
        return True
    except Exception as exc:
        print(f"      failed: {exc}")
        print("      Manual fallback: grab silero_vad.onnx from")
        print("      https://github.com/snakers4/silero-vad/tree/master/src/silero_vad/data")
        print(f"      and put it in {MODELS}")
        return False


def main() -> int:
    MODELS.mkdir(exist_ok=True)
    ok = download_wake_models()
    ok = download_silero() and ok
    ok = True and ok
    print("\nAll set." if ok else "\nSome downloads failed — see the notes above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
