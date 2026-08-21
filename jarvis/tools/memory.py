"""
Memory (Phase E, spec G53-G58): local semantic recall, no cloud, no quota.

fastembed (BAAI/bge-small-en-v1.5, 384-dim) for embeddings — pure ONNX, no
PyTorch, matching every other model in this project. sqlite-vec for
storage and nearest-neighbour search — one .db file, no service to run.
Both confirmed live on this machine before this module was written: the
model loads and embeds correctly, and a real semantic query ("what do I
put in my coffee") correctly ranked a stored memory about coffee closest
among three candidates, not just returned something without crashing.

"No secrets written into memory" (handoff §E, non-negotiable rule #4) is
enforced here, not just hoped for: remember() refuses text that looks like
a credential before it ever reaches the embedding model or the database.
This is a heuristic, not a guarantee — it catches the shapes real secrets
take (long random-looking tokens, "api_key: ...", etc.), not everything
a secret could ever look like. The real backstop is the same one
everywhere else in this project: don't hand Jalen something you don't
want remembered.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent  # jarvis/tools/memory.py -> project root

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

_SECRET_PATTERNS = [
    # keyword, then up to a few filler words ("is", "for my account"), an
    # optional separator, then a value of some real length. Found live:
    # "password: X" alone missed "my wifi password is: X" — the filler
    # words in between are how people actually phrase these out loud.
    re.compile(
        r"\b(api[_-]?key|secret|password|passwd|credential|pin\s*code)s?\b"
        r"(?:\s+\w+){0,3}?\s*[:=]?\s+\S{6,}",
        re.I,
    ),
    re.compile(r"\bsk-[a-zA-Z0-9]{16,}"),          # OpenAI-style
    re.compile(r"\bgsk_[a-zA-Z0-9]{16,}"),         # Groq
    re.compile(r"\bghp_[a-zA-Z0-9]{16,}"),         # GitHub PAT
    re.compile(r"\b\d{13,19}\b"),                  # something card-number-shaped
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),        # a long opaque token-shaped string
]


def looks_like_a_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


class MemoryStore:
    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("memory.enabled", True))
        self.top_k = int(cfg.get_path("memory.recall_top_k", 6))
        self.embed_model_name = cfg.get_path("memory.embed_model", "BAAI/bge-small-en-v1.5")
        self.db_path = ROOT / cfg.get_path("memory.db_path", "data/jarvis.db")
        self._lock = threading.Lock()
        self._model = None  # lazily loaded — costs nothing until first remember()/recall()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ setup
    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        import sqlite_vec

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(_SCHEMA)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(embedding float[384])")
        conn.commit()
        self._conn = conn
        return conn

    def _embed_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.embed_model_name)
        return self._model

    def _embed(self, text: str) -> list[float]:
        return next(iter(self._embed_model().embed([text]))).tolist()

    # ---------------------------------------------------------------- remember
    def remember(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "There's nothing there to remember."
        if not self.enabled:
            return "Memory is off right now."
        if looks_like_a_secret(text):
            return "That looks like it might be a credential — I'm not storing that."

        import sqlite_vec

        with self._lock:
            conn = self._connection()
            embedding = self._embed(text)
            cur = conn.execute(
                "INSERT INTO memories (text, created_at) VALUES (?, ?)",
                (text, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            row_id = cur.lastrowid
            conn.execute(
                "INSERT INTO memory_vectors (rowid, embedding) VALUES (?, ?)",
                (row_id, sqlite_vec.serialize_float32(embedding)),
            )
            conn.commit()
        return "Got it, I'll remember that."

    # ------------------------------------------------------------------ recall
    def recall(self, query: str, top_k: int | None = None) -> str:
        query = (query or "").strip()
        if not query:
            return "Recall what, exactly?"
        if not self.enabled:
            return "Memory is off right now."

        import sqlite_vec

        k = top_k or self.top_k
        with self._lock:
            conn = self._connection()
            if conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0:
                return "I don't have anything stored yet."
            embedding = self._embed(query)
            rows = conn.execute(
                "SELECT m.text, v.distance FROM memory_vectors v "
                "JOIN memories m ON m.id = v.rowid "
                "WHERE v.embedding MATCH ? AND k = ? "
                "ORDER BY v.distance",
                (sqlite_vec.serialize_float32(embedding), k),
            ).fetchall()

        if not rows:
            return "Nothing relevant comes to mind."
        return "\n".join(f"- {text}" for text, _distance in rows)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


# --------------------------------------------------------------------- tools
_STORE: MemoryStore | None = None


def _store() -> MemoryStore:
    global _STORE
    if _STORE is None:
        from ..config import CONFIG

        _STORE = MemoryStore(CONFIG)
    return _STORE


def remember(text: str) -> str:
    return _store().remember(text)


def recall_memory(query: str) -> str:
    return _store().recall(query)


REGISTRY: dict[str, Any] = {
    "remember": remember,
    "recall_memory": recall_memory,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
