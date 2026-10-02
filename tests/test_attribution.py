from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from safety.attribution import build_envelope, content_checksum, load_json, mutate, verify_envelope
from safety.workset import accept, counts, incident, initialize

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT = object()


class AttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.descriptors = load_json(ROOT / "fixtures" / "datasets.json")
        self.registry = load_json(ROOT / "fixtures" / "trust_registry.json")
        self.signing_key = load_json(ROOT / "fixtures" / "source_signing_keys.json")["dataset_source"]
        self.manifest = load_json(ROOT / "fixtures" / "trusted_manifest.json")
        self.run_id, self.message_id = "test-run", str(uuid.uuid4())
        identity = self.registry["dataset_source"]
        self.envelope = build_envelope(self.descriptors, source_agent_id="dataset_source", principal=identity["principal"], key_id=identity["key_id"], private_seed_hex=self.signing_key["private_seed_hex"], run_id=self.run_id, message_id=self.message_id)

    def verify(self, envelope=_DEFAULT, descriptors=_DEFAULT):
        return verify_envelope(self.envelope if envelope is _DEFAULT else envelope, self.descriptors if descriptors is _DEFAULT else descriptors, self.registry, self.manifest, expected_run_id=self.run_id, expected_message_id=self.message_id)

    def test_clean_envelope_is_accepted(self) -> None:
        result = self.verify()
        self.assertTrue(result.accepted)
        self.assertEqual(result.authenticated_source, "dataset_source")

    def test_missing_and_tampered_provenance_are_rejected(self) -> None:
        self.assertEqual(self.verify(envelope=None).incident_code, "missing_provenance")
        substituted = mutate(self.descriptors)
        substituted[0]["checksum"] = "sha256:tampered"
        self.assertEqual(self.verify(descriptors=substituted).incident_code, "content_substitution")

    def test_identity_and_manifest_failures_are_distinguished(self) -> None:
        forged = mutate(self.envelope)
        forged["source_agent_id"] = "index_operator"
        forged["principal"] = "example-index-operator"
        forged["key_id"] = "index-operator-2026-01"
        self.assertEqual(self.verify(envelope=forged).incident_code, "invalid_signature")
        changed = mutate(self.descriptors)
        changed[0]["checksum"] = "sha256:wrong"
        identity = self.registry["dataset_source"]
        signed_changed = build_envelope(changed, source_agent_id="dataset_source", principal=identity["principal"], key_id=identity["key_id"], private_seed_hex=self.signing_key["private_seed_hex"], run_id=self.run_id, message_id=self.message_id)
        self.assertEqual(self.verify(envelope=signed_changed, descriptors=changed).incident_code, "manifest_conflict")

    def test_descriptor_checksums_match_local_dataset_content(self) -> None:
        content = ROOT / "fixtures" / "dataset-content"
        for descriptor in self.descriptors:
            filename = descriptor["dataset_id"] + ".json"
            self.assertEqual(descriptor["checksum"], content_checksum((content / filename).read_bytes()))

    def test_rejection_leaves_no_accepted_workset_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workset.sqlite"
            initialize(path)
            incident(path, self.message_id, "missing_provenance", {"reason": "fixture"})
            self.assertEqual(counts(path), {"accepted_batches": 0, "incidents": 1})
            accept(path, "second", "dataset_source", self.envelope, self.descriptors)
            self.assertEqual(counts(path), {"accepted_batches": 1, "incidents": 1})

    def test_accept_is_idempotent_by_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workset.sqlite"
            initialize(path)
            self.assertTrue(accept(path, self.message_id, "dataset_source", self.envelope, self.descriptors))
            self.assertFalse(accept(path, self.message_id, "dataset_source", self.envelope, self.descriptors))
            self.assertEqual(counts(path), {"accepted_batches": 1, "incidents": 0})


if __name__ == "__main__":
    unittest.main()
