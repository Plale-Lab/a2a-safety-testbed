"""Digest-linked, signed transformation lineage for the multi-hop study."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from safety.attribution import canonical_json

LINEAGE_VERSION = "beacon-lineage-v1"


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_json(value)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def record_digest(record: dict[str, Any]) -> str:
    return digest(record)


def append_record(
    lineage: list[dict[str, Any]], *, agent_id: str, identity: dict[str, str],
    private_seed_hex: str, transformation: str, input_value: Any,
    output_value: Any, run_id: str, delivery_key: str,
) -> dict[str, Any]:
    signed = {
        "version": LINEAGE_VERSION,
        "sequence": len(lineage),
        "agent_id": agent_id,
        "principal": identity["principal"],
        "key_id": identity["key_id"],
        "transformation": transformation,
        "input_digest": digest(input_value),
        "output_digest": digest(output_value),
        "previous_record_digest": record_digest(lineage[-1]) if lineage else None,
        "run_id": run_id,
        "delivery_key": delivery_key,
    }
    signature = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_seed_hex)).sign(canonical_json(signed))
    record = {**signed, "signature": base64.b64encode(signature).decode("ascii")}
    lineage.append(record)
    return record


@dataclass(frozen=True)
class LineageVerdict:
    accepted: bool
    reason: str
    incident_code: str | None
    verified_records: int
    expected_records: int

    @property
    def completeness(self) -> float:
        return self.verified_records / self.expected_records


def verify_lineage(
    lineage: list[dict[str, Any]], trust_registry: dict[str, Any],
    expected_agents: tuple[str, ...], *, expected_run_id: str,
) -> LineageVerdict:
    expected_count = len(expected_agents)
    if len(lineage) != expected_count:
        # Continue through the contiguous prefix to distinguish a missing middle
        # record from a merely truncated tail.
        pass
    previous: dict[str, Any] | None = None
    verified = 0
    for index, record in enumerate(lineage):
        if record.get("sequence") != index:
            missing_record = len(lineage) < expected_count and record.get("sequence", -1) > index
            code = "missing_intermediate_record" if missing_record else "record_order_failure"
            return LineageVerdict(False, "lineage sequence is not contiguous", code, verified, expected_count)
        if index >= expected_count or record.get("agent_id") != expected_agents[index]:
            return LineageVerdict(False, "agent order differs from the authorized chain", "agent_order_failure", verified, expected_count)
        if record.get("run_id") != expected_run_id or record.get("version") != LINEAGE_VERSION:
            return LineageVerdict(False, "lineage record binding or version is invalid", "record_binding_failure", verified, expected_count)
        expected_previous = record_digest(previous) if previous else None
        if record.get("previous_record_digest") != expected_previous:
            return LineageVerdict(False, "record does not bind to its predecessor", "digest_continuity_failure", verified, expected_count)
        if previous and record.get("input_digest") != previous.get("output_digest"):
            return LineageVerdict(False, "transformation input does not match prior output", "content_continuity_failure", verified, expected_count)
        identity = trust_registry.get(record.get("agent_id"))
        if not identity or record.get("principal") != identity["principal"] or record.get("key_id") != identity["key_id"]:
            return LineageVerdict(False, "record signer is not authorized", "identity_binding_failure", verified, expected_count)
        signed = {key: value for key, value in record.items() if key != "signature"}
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity["public_key_hex"])).verify(
                base64.b64decode(record["signature"], validate=True), canonical_json(signed)
            )
        except (KeyError, ValueError, InvalidSignature):
            return LineageVerdict(False, "record signature is invalid", "invalid_signature", verified, expected_count)
        verified += 1
        previous = record
    if len(lineage) != expected_count:
        return LineageVerdict(False, "lineage ends before the authorized chain is complete", "incomplete_lineage", verified, expected_count)
    return LineageVerdict(True, "all contributors, signatures, transformations, and links verified", None, verified, expected_count)


def initialize_store(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS verified_lineages (delivery_key TEXT PRIMARY KEY, run_id TEXT, lineage_json TEXT)")


def persist_verified(path: Path, delivery_key: str, run_id: str, lineage: list[dict[str, Any]]) -> bool:
    initialize_store(path)
    with sqlite3.connect(path) as db:
        cursor = db.execute("INSERT OR IGNORE INTO verified_lineages VALUES (?, ?, ?)", (delivery_key, run_id, canonical_json(lineage).decode()))
        return cursor.rowcount == 1


def persisted_count(path: Path) -> int:
    initialize_store(path)
    with sqlite3.connect(path) as db:
        return int(db.execute("SELECT COUNT(*) FROM verified_lineages").fetchone()[0])
