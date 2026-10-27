"""SQLite history of everything VisionVoice did (metadata only; no page text or audio)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    kind          TEXT NOT NULL,          -- capture | summary | command | session
    source        TEXT,                   -- voice | tap | typed | dashboard | upload
    detail        TEXT,                   -- command text / summary target
    expected_page INTEGER,
    detected_page INTEGER,
    decision      TEXT,                   -- MATCH | MISMATCH | UNCERTAIN | ANSWERED | NOT_FOUND ...
    page_status   TEXT,                   -- DETECTED | MISSING | AMBIGUOUS | UNREADABLE
    confidence    REAL,
    ocr_ms        INTEGER,
    capture_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ops_ts ON operations(ts);
"""

COLUMNS = ("kind", "source", "detail", "expected_page", "detected_page", "decision", "page_status", "confidence",
           "ocr_ms", "capture_id")


class History:
    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        if str(p) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def add(self, kind: str, **fields) -> dict:
        row = {"ts": time.time(), "kind": kind, **{k: fields.get(k) for k in COLUMNS if k != "kind"}}
        with self.lock:
            cur = self.db.execute(
                f"INSERT INTO operations (ts, {', '.join(COLUMNS)}) VALUES (?, {', '.join('?' * len(COLUMNS))})",
                [row["ts"]] + [row.get(c) for c in COLUMNS],
            )
            self.db.commit()
            row["id"] = cur.lastrowid
        return row

    def recent(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.lock:
            dec = dict(self.db.execute(
                "SELECT decision, COUNT(*) FROM operations WHERE kind='capture' GROUP BY decision").fetchall())
            avg = self.db.execute("SELECT AVG(ocr_ms) FROM operations WHERE kind='capture' AND ocr_ms IS NOT NULL").fetchone()[0]
            summ = self.db.execute("SELECT COUNT(*) FROM operations WHERE kind='summary'").fetchone()[0]
            cmds = self.db.execute("SELECT COUNT(*) FROM operations WHERE kind='command'").fetchone()[0]
        return {
            "match": dec.get("MATCH", 0), "mismatch": dec.get("MISMATCH", 0), "uncertain": dec.get("UNCERTAIN", 0),
            "captures": sum(dec.values()), "avg_ocr_ms": round(avg) if avg else None, "summaries": summ, "commands": cmds,
        }
