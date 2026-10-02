from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from safety.attribution import load_json
from safety.lineage import append_record, persist_verified, persisted_count, verify_lineage

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ("retrieval_agent", "transformation_agent", "summary_agent")


def chain(run_id: str = "test-lineage"):
    registry = load_json(ROOT / "fixtures" / "lineage_trust_registry.json")
    keys = load_json(ROOT / "fixtures" / "lineage_signing_keys.json")
    values = [
        {"datasets": ["air-quality-north", "air-quality-south"]},
        {"datasets": ["air-quality-north", "air-quality-south"], "filter": "quality-controlled"},
        {"summary": "two quality-controlled air-quality datasets"},
        {"final": "two quality-controlled air-quality datasets"},
    ]
    transformations = ("retrieve", "filter", "summarize")
    result = []
    for index, agent in enumerate(AGENTS):
        append_record(result, agent_id=agent, identity=registry[agent], private_seed_hex=keys[agent]["private_seed_hex"], transformation=transformations[index], input_value=values[index], output_value=values[index + 1], run_id=run_id, delivery_key=f"{run_id}:{agent}")
    return result, registry


class LineageTests(unittest.TestCase):
    def test_clean_chain_verifies(self):
        records, registry = chain()
        verdict = verify_lineage(records, registry, AGENTS, expected_run_id="test-lineage")
        self.assertTrue(verdict.accepted)
        self.assertEqual(verdict.completeness, 1.0)

    def test_missing_middle_record_is_detected(self):
        records, registry = chain()
        verdict = verify_lineage([records[0], records[2]], registry, AGENTS, expected_run_id="test-lineage")
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.incident_code, "missing_intermediate_record")
        self.assertEqual(verdict.verified_records, 1)

    def test_post_signing_transformation_mutation_is_invalid_signature(self):
        records, registry = chain()
        records[1]["transformation"] = "aggregate-after-signing"
        self.assertEqual(verify_lineage(records, registry, AGENTS, expected_run_id="test-lineage").incident_code, "invalid_signature")

    def test_reordering_is_distinct_from_missing_record(self):
        records, registry = chain()
        reordered = [records[0], records[2], records[1]]
        self.assertEqual(verify_lineage(reordered, registry, AGENTS, expected_run_id="test-lineage").incident_code, "record_order_failure")

    def test_digest_break_is_detected(self):
        records, registry = chain()
        records[1]["previous_record_digest"] = "sha256:altered"
        self.assertEqual(verify_lineage(records, registry, AGENTS, expected_run_id="test-lineage").incident_code, "digest_continuity_failure")

    def test_verified_lineage_persistence_is_idempotent(self):
        records, _ = chain()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lineage.sqlite"
            self.assertTrue(persist_verified(path, "logical-chain", "test-lineage", records))
            self.assertFalse(persist_verified(path, "logical-chain", "test-lineage", records))
            self.assertEqual(persisted_count(path), 1)


if __name__ == "__main__":
    unittest.main()
