"""Canonical, signed provenance envelopes and independent verification.

This module intentionally keeps protocol-extension data in ordinary A2A metadata
so the harness can compare stock handling with application-level enforcement.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ENVELOPE_VERSION = "beacon-provenance-v1"


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    reason: str
    incident_code: str | None = None
    authenticated_source: str | None = None
    claimed_source: str | None = None


def canonical_json(value: Any) -> bytes:
    """The wire-independent bytes covered by each signature."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def content_checksum(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _private_key(seed_hex: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))


def build_envelope(
    descriptors: list[dict[str, Any]], *, source_agent_id: str, principal: str,
    key_id: str, private_seed_hex: str, run_id: str, message_id: str,
) -> dict[str, Any]:
    signed = {
        "version": ENVELOPE_VERSION,
        "descriptors": descriptors,
        "source_agent_id": source_agent_id,
        "principal": principal,
        "key_id": key_id,
        "run_id": run_id,
        "message_id": message_id,
    }
    signature = _private_key(private_seed_hex).sign(canonical_json(signed))
    return {**signed, "signature": base64.b64encode(signature).decode("ascii")}


def verify_envelope(
    envelope: dict[str, Any] | None, submitted_descriptors: list[dict[str, Any]],
    trust_registry: dict[str, Any], trusted_manifest: dict[str, Any],
    *, expected_run_id: str | None = None, expected_message_id: str | None = None,
) -> VerificationResult:
    if not envelope:
        return VerificationResult(False, "provenance envelope is missing", "missing_provenance")
    claimed_source = envelope.get("source_agent_id")
    if envelope.get("version") != ENVELOPE_VERSION:
        return VerificationResult(False, "unsupported provenance envelope version", "invalid_envelope", claimed_source=claimed_source)
    if expected_run_id is not None and envelope.get("run_id") != expected_run_id:
        return VerificationResult(False, "run identifier is not bound to this evaluation", "message_binding_failure", claimed_source=claimed_source)
    if expected_message_id is not None and envelope.get("message_id") != expected_message_id:
        return VerificationResult(False, "message identifier is not bound to this message", "message_binding_failure", claimed_source=claimed_source)
    identity = trust_registry.get(claimed_source)
    if not identity:
        return VerificationResult(False, "signing identity is not in the trust registry", "unknown_key", claimed_source=claimed_source)
    if envelope.get("principal") != identity["principal"] or envelope.get("key_id") != identity["key_id"]:
        return VerificationResult(False, "claimed identity does not match its trusted binding", "identity_binding_failure", claimed_source=claimed_source)
    signed = {key: value for key, value in envelope.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity["public_key_hex"])).verify(
            base64.b64decode(envelope["signature"], validate=True), canonical_json(signed)
        )
    except (KeyError, ValueError, InvalidSignature):
        return VerificationResult(False, "signature cannot be verified for the claimed identity", "invalid_signature", claimed_source=claimed_source)
    authenticated_source = claimed_source
    if envelope.get("descriptors") != submitted_descriptors:
        return VerificationResult(False, "submitted descriptors differ from signed descriptors", "content_substitution", authenticated_source, claimed_source)
    permitted = {(item["dataset_id"], item["version"]): item for item in trusted_manifest["datasets"]}
    for descriptor in submitted_descriptors:
        expected = permitted.get((descriptor.get("dataset_id"), descriptor.get("version")))
        if expected is None or descriptor.get("checksum") != expected["checksum"]:
            return VerificationResult(False, "descriptor conflicts with the independently trusted manifest", "manifest_conflict", authenticated_source, claimed_source)
    return VerificationResult(True, "signature, identity binding, and manifest all verified", authenticated_source=authenticated_source, claimed_source=claimed_source)


def mutate(value: Any) -> Any:
    """Explicit deep-copy helper for incident scenarios."""
    return copy.deepcopy(value)
