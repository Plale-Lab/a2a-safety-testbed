"""Three-agent signed-lineage study with structural and integrity attacks."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ray

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from safety.attribution import canonical_json, load_json  # noqa: E402
from safety.lineage import append_record, persist_verified, persisted_count, verify_lineage  # noqa: E402

AGENTS = ("retrieval_agent", "transformation_agent", "summary_agent")
TRANSFORMATIONS = ("retrieve", "filter", "summarize")
SCENARIOS = ("clean", "missing-middle-record", "altered-transformation", "reordered-records")
EXPECTED_INCIDENTS = {
    "missing-middle-record": "missing_intermediate_record",
    "altered-transformation": "invalid_signature",
    "reordered-records": "record_order_failure",
}


def build_chain(run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry = load_json(ROOT / "fixtures" / "lineage_trust_registry.json")
    keys = load_json(ROOT / "fixtures" / "lineage_signing_keys.json")
    values = [
        {"query": "air quality", "source": "fixture catalog"},
        {"datasets": ["air-quality-north", "air-quality-south"]},
        {"datasets": ["air-quality-north", "air-quality-south"], "filter": "quality-controlled"},
        {"summary": "two quality-controlled air-quality datasets"},
    ]
    lineage: list[dict[str, Any]] = []
    for index, agent in enumerate(AGENTS):
        append_record(
            lineage,
            agent_id=agent,
            identity=registry[agent],
            private_seed_hex=keys[agent]["private_seed_hex"],
            transformation=TRANSFORMATIONS[index],
            input_value=values[index],
            output_value=values[index + 1],
            run_id=run_id,
            delivery_key=f"{run_id}:{agent}",
        )
    return lineage, registry


def run_trial(scenario: str, repetition: int, output: str) -> dict[str, Any]:
    run_id = f"lineage-{scenario}-{repetition}-{uuid.uuid4()}"
    complete, registry = build_chain(run_id)
    if scenario == "clean":
        submitted = complete
    elif scenario == "missing-middle-record":
        submitted = [complete[0], complete[2]]
    elif scenario == "altered-transformation":
        complete[1]["transformation"] = "aggregate-after-signing"
        submitted = complete
    elif scenario == "reordered-records":
        submitted = [complete[0], complete[2], complete[1]]
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    baseline_started = time.perf_counter_ns()
    baseline_accepted = bool(submitted)
    baseline_ms = (time.perf_counter_ns() - baseline_started) / 1_000_000
    verification_started = time.perf_counter_ns()
    verdict = verify_lineage(submitted, registry, AGENTS, expected_run_id=run_id)
    verification_ms = (time.perf_counter_ns() - verification_started) / 1_000_000

    first_persisted = retry_persisted = False
    durable_rows = 0
    if verdict.accepted:
        store = Path(output) / f"{scenario}-{repetition}.sqlite"
        logical_key = f"verified:{run_id}"
        first_persisted = persist_verified(store, logical_key, run_id, submitted)
        retry_persisted = persist_verified(store, logical_key, run_id, submitted)
        durable_rows = persisted_count(store)

    return {
        "scenario": scenario,
        "repetition": repetition,
        "run_id": run_id,
        "baseline_accepted": baseline_accepted,
        "accepted": verdict.accepted,
        "incident_code": verdict.incident_code,
        "reason": verdict.reason,
        "verified_records": verdict.verified_records,
        "expected_records": verdict.expected_records,
        "lineage_completeness": verdict.completeness,
        "baseline_decision_ms": baseline_ms,
        "verification_ms": verification_ms,
        "verification_overhead_ms": max(0.0, verification_ms - baseline_ms),
        "lineage_bytes": len(canonical_json(submitted)),
        "first_persisted": first_persisted,
        "retry_persisted": retry_persisted,
        "durable_rows": durable_rows,
    }


run_trial_remote = ray.remote(num_cpus=1)(run_trial)


def summarize(rows: list[dict[str, Any]], wall_seconds: float, workers: int) -> dict[str, Any]:
    clean = [row for row in rows if row["scenario"] == "clean"]
    attacks = {scenario: [row for row in rows if row["scenario"] == scenario] for scenario in EXPECTED_INCIDENTS}
    attacked = [row for scenario_rows in attacks.values() for row in scenario_rows]
    verification = [row["verification_ms"] for row in rows]
    overhead = [row["verification_overhead_ms"] for row in rows]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conditions": {"baseline": "unmodified A2A without provenance verification", "provenance_enabled": "signed digest-linked lineage verification"},
        "agents": list(AGENTS),
        "repetitions_per_scenario": len(clean),
        "workers": workers,
        "local_wall_seconds": wall_seconds,
        "clean_acceptance_rate": sum(row["accepted"] for row in clean) / len(clean),
        "baseline_attack_acceptance_rate": sum(row["baseline_accepted"] for row in attacked) / len(attacked),
        "per_attack_detection_rate": {
            scenario: sum(not row["accepted"] and row["incident_code"] == EXPECTED_INCIDENTS[scenario] for row in scenario_rows) / len(scenario_rows)
            for scenario, scenario_rows in attacks.items()
        },
        "per_attack_incident_code": EXPECTED_INCIDENTS,
        "overall_attack_detection_rate": sum(not row["accepted"] and row["incident_code"] == EXPECTED_INCIDENTS[row["scenario"]] for row in attacked) / len(attacked),
        "false_rejection_rate": sum(not row["accepted"] for row in clean) / len(clean),
        "clean_lineage_completeness": statistics.mean(row["lineage_completeness"] for row in clean),
        "per_attack_verified_prefix_completeness": {scenario: statistics.mean(row["lineage_completeness"] for row in scenario_rows) for scenario, scenario_rows in attacks.items()},
        "median_verification_ms": statistics.median(verification),
        "p95_verification_ms": sorted(verification)[round((len(verification) - 1) * .95)],
        "median_verification_overhead_ms": statistics.median(overhead),
        "median_clean_lineage_bytes": statistics.median(row["lineage_bytes"] for row in clean),
        "durable_consistency_rate": sum(row["first_persisted"] and not row["retry_persisted"] and row["durable_rows"] == 1 for row in clean) / len(clean),
        "single_machine_limitation": "Ray schedules independent local trials; timing does not establish multi-node scalability. Baseline decision timing is a minimal pass-through comparison.",
    }
    all_attacks_detected = all(rate == 1.0 for rate in summary["per_attack_detection_rate"].values())
    if not (summary["clean_acceptance_rate"] == summary["baseline_attack_acceptance_rate"] == summary["overall_attack_detection_rate"] == summary["durable_consistency_rate"] == 1.0 and all_attacks_detected and summary["false_rejection_rate"] == 0.0):
        raise RuntimeError(f"lineage experiment invariant failed: {summary}")
    return summary


def markdown(summary: dict[str, Any]) -> str:
    return f"""# Three-agent provenance-lineage experiment

| Measure | Result |
| --- | ---: |
| Clean acceptance | {summary['clean_acceptance_rate']:.0%} |
| Attacks accepted by baseline condition | {summary['baseline_attack_acceptance_rate']:.0%} |
| Missing-middle detection (`missing_intermediate_record`) | {summary['per_attack_detection_rate']['missing-middle-record']:.0%} |
| Altered-transform detection (`invalid_signature`) | {summary['per_attack_detection_rate']['altered-transformation']:.0%} |
| Reordered-record detection (`record_order_failure`) | {summary['per_attack_detection_rate']['reordered-records']:.0%} |
| Overall attack detection | {summary['overall_attack_detection_rate']:.0%} |
| False rejection | {summary['false_rejection_rate']:.0%} |
| Clean lineage completeness | {summary['clean_lineage_completeness']:.0%} |
| Median verification | {summary['median_verification_ms']:.3f} ms |
| p95 verification | {summary['p95_verification_ms']:.3f} ms |
| Median verification overhead vs minimal pass-through | {summary['median_verification_overhead_ms']:.3f} ms |
| Median signed lineage size | {summary['median_clean_lineage_bytes']:.0f} bytes |
| Durable consistency under retry | {summary['durable_consistency_rate']:.0%} |

The experiment ran on one machine. Local task timing is not evidence of multi-node scalability, and signatures authenticate registered keys and record integrity—not the truth of transformed content.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.repetitions < 1 or args.workers < 1:
        parser.error("--repetitions and --workers must be positive")
    output = ROOT / "results" / "lineage" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True)
    ray.init(num_cpus=args.workers, include_dashboard=False, log_to_driver=False)
    try:
        started = time.perf_counter()
        refs = [run_trial_remote.remote(scenario, repetition, str(output)) for scenario in SCENARIOS for repetition in range(args.repetitions)]
        rows = ray.get(refs)
        wall_seconds = time.perf_counter() - started
    finally:
        ray.shutdown()
    rows.sort(key=lambda row: (SCENARIOS.index(row["scenario"]), row["repetition"]))
    result = {"summary": summarize(rows, wall_seconds, args.workers), "observations": rows}
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output / "RESULTS.md").write_text(markdown(result["summary"]), encoding="utf-8")
    (ROOT / "lineage-demo-results.json").write_text(json.dumps(result["summary"], indent=2) + "\n", encoding="utf-8")
    print(output / "RESULTS.md")


if __name__ == "__main__":
    main()
