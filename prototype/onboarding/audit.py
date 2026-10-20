"""Append-only, hash-chained audit log.

Each record embeds the hash of the previous one, so any edit/delete/reorder is
detectable by verify(). In production: ship each record to S3 Object Lock
(WORM, compliance mode) + CloudWatch/SIEM; keep this chain as the integrity proof.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "GENESIS"


def _digest(rec: dict) -> str:
    return hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prev = GENESIS
        if self.path.exists() and self.path.stat().st_size:
            self.prev = json.loads(self.path.read_text().strip().splitlines()[-1])["hash"]

    def write(self, run_id: str, actor: str, action: str, **detail) -> dict:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": run_id, "actor": actor, "action": action,
            "detail": detail, "prev": self.prev,
        }
        rec["hash"] = _digest(rec)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.prev = rec["hash"]
        return rec

    def records(self, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        recs = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        return [r for r in recs if run_id in (None, r["run_id"])]

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, rec in enumerate(self.records()):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec["prev"] != prev or _digest(body) != rec["hash"]:
                return False, f"chain broken at record {i}"
            prev = rec["hash"]
        return True, "ok"
