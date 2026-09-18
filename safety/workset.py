"""Atomic local workset persistence for the attribution evaluation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def initialize(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS accepted_batches (message_id TEXT PRIMARY KEY, source_agent_id TEXT, envelope_json TEXT, descriptors_json TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS incidents (message_id TEXT, code TEXT, detail_json TEXT)")


def accept(path: Path, message_id: str, source_agent_id: str | None, envelope: dict[str, Any] | None, descriptors: list[dict[str, Any]]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO accepted_batches VALUES (?, ?, ?, ?)", (message_id, source_agent_id, json.dumps(envelope, sort_keys=True), json.dumps(descriptors, sort_keys=True)))


def incident(path: Path, message_id: str, code: str, detail: dict[str, Any]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO incidents VALUES (?, ?, ?)", (message_id, code, json.dumps(detail, sort_keys=True)))


def counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as db:
        return {"accepted_batches": db.execute("SELECT COUNT(*) FROM accepted_batches").fetchone()[0], "incidents": db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]}
