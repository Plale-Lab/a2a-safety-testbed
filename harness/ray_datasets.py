"""Local Ray Core runner for the existing dataset attribution matrix.

Ray parallelizes independent condition/scenario cells. Each task starts one
local A2A index process and owns one SQLite file; no server or database is
shared across tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ray

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.datasets import SCENARIOS, free_port, run_condition, send, summary, validate_observations, wait_ready  # noqa: E402
from safety.workset import counts  # noqa: E402

CONDITIONS = ("stock", "extended")


@ray.remote(num_cpus=1)
def run_cell(condition: str, scenario: str, repetitions: int, output: str) -> list[dict[str, Any]]:
    return run_condition(condition, scenario, repetitions, Path(output))


def ordered(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    condition_order = {name: index for index, name in enumerate(CONDITIONS)}
    scenario_order = {name: index for index, name in enumerate(SCENARIOS)}
    return sorted(observations, key=lambda row: (condition_order[row["condition"]], scenario_order[row["scenario"]], row["repetition_index"]))


def execute(mode: str, repetitions: int, output: Path, workers: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cpu_before = time.process_time()
    started = time.perf_counter()
    if mode == "sequential":
        cells = [run_condition(condition, scenario, repetitions, output) for condition in CONDITIONS for scenario in SCENARIOS]
    else:
        ray.init(num_cpus=workers, include_dashboard=False, log_to_driver=False)
        try:
            refs = [run_cell.remote(condition, scenario, repetitions, str(output)) for condition in CONDITIONS for scenario in SCENARIOS]
            cells = ray.get(refs)
        finally:
            ray.shutdown()
    wall_seconds = time.perf_counter() - started
    observations = ordered([item for cell in cells for item in cell])
    validate_observations(observations)
    metrics = {
        "mode": mode,
        "workers": 1 if mode == "sequential" else workers,
        "cells": len(CONDITIONS) * len(SCENARIOS),
        "observations": len(observations),
        "wall_seconds": wall_seconds,
        "cells_per_second": len(CONDITIONS) * len(SCENARIOS) / wall_seconds,
        "observations_per_second": len(observations) / wall_seconds,
        "driver_cpu_seconds": time.process_time() - cpu_before,
        # macOS reports ru_maxrss in bytes. This is the driver's peak, not a
        # sum of Ray workers or the A2A child services.
        "driver_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024),
    }
    return observations, metrics


def duplicate_delivery(output: Path) -> dict[str, Any]:
    """Deliver clean work twice with one key, then twice with distinct keys."""
    import subprocess

    port = free_port()
    workset = output / "duplicate-delivery.sqlite"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "agents" / "dataset_index.py"), "--port", str(port), "--condition", "extended", "--workset", str(workset)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_ready(port)
        stable_key = f"duplicate-{uuid.uuid4()}"
        first = asyncio.run(send(port, "clean", "duplicate-demo", stable_key))
        retry = asyncio.run(send(port, "clean", "duplicate-demo", stable_key))
        after_retry = counts(workset)
        control_a = asyncio.run(send(port, "clean", "control-a", f"control-{uuid.uuid4()}"))
        control_b = asyncio.run(send(port, "clean", "control-b", f"control-{uuid.uuid4()}"))
        after_control = counts(workset)
        result = {
            "idempotency_key": stable_key,
            "transport_ids_distinct": first["transport_message_id"] != retry["transport_message_id"],
            "first_processed": first["decision"]["processed"],
            "retry_processed": retry["decision"]["processed"],
            "retry_marked_duplicate": retry["decision"]["duplicate"],
            "rows_after_two_same_key_deliveries": after_retry["accepted_batches"],
            "rows_after_two_additional_unique_deliveries": after_control["accepted_batches"],
            "control_processed": [control_a["decision"]["processed"], control_b["decision"]["processed"]],
        }
        expected = {
            "transport_ids_distinct": True,
            "first_processed": True,
            "retry_processed": False,
            "retry_marked_duplicate": True,
            "rows_after_two_same_key_deliveries": 1,
            "rows_after_two_additional_unique_deliveries": 3,
            "control_processed": [True, True],
        }
        if any(result[key] != value for key, value in expected.items()):
            raise RuntimeError(f"duplicate-delivery invariant failed: {result}")
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def comparison_markdown(metrics: list[dict[str, Any]], duplicate: dict[str, Any], observations: list[dict[str, Any]]) -> str:
    by_mode = {row["mode"]: row for row in metrics}
    sequential, parallel = by_mode["sequential"], by_mode["ray"]
    speedup = sequential["wall_seconds"] / parallel["wall_seconds"]
    lines = [
        "# Local Ray Core comparison",
        "",
        "| Mode | Workers | Matrix wall time | Cells/s | Observations/s | Driver CPU | Driver peak RSS |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in metrics:
        lines.append(f"| {row['mode']} | {row['workers']} | {row['wall_seconds']:.2f} s | {row['cells_per_second']:.2f} | {row['observations_per_second']:.2f} | {row['driver_cpu_seconds']:.2f} s | {row['driver_peak_rss_mib']:.1f} MiB |")
    lines.extend([
        "",
        f"Observed local matrix speedup: **{speedup:.2f}x**. All {len(observations)} Ray observations matched the sequential acceptance policy.",
        "",
        "## Duplicate delivery",
        "",
        f"Two transport messages carrying the same application idempotency key produced **{duplicate['rows_after_two_same_key_deliveries']} persisted row**. The retry returned `processed={str(duplicate['retry_processed']).lower()}` and `duplicate={str(duplicate['retry_marked_duplicate']).lower()}`. Two additional unique keys increased the row count to {duplicate['rows_after_two_additional_unique_deliveries']}.",
        "",
        "CPU and RSS are lightweight driver-process observations, not whole-machine or cluster profiles. Ray startup is included in wall time. This single-machine run does not establish multi-node scalability.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.repetitions < 1 or args.workers < 1:
        parser.error("--repetitions and --workers must both be positive")
    run_dir = ROOT / "results" / "ray" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sequential_dir, ray_dir = run_dir / "sequential", run_dir / "ray"
    sequential_dir.mkdir(parents=True)
    ray_dir.mkdir()

    sequential, sequential_metrics = execute("sequential", args.repetitions, sequential_dir, args.workers)
    parallel, ray_metrics = execute("ray", args.repetitions, ray_dir, args.workers)
    duplicate = duplicate_delivery(run_dir)
    if [(r["condition"], r["scenario"], bool(r["decision"]["accepted"])) for r in sequential] != [(r["condition"], r["scenario"], bool(r["decision"]["accepted"])) for r in parallel]:
        raise RuntimeError("Ray outcomes differ from the sequential baseline")

    document = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ray_version": ray.__version__,
        "python_version": sys.version.split()[0],
        "host": {"logical_cpus": os.cpu_count(), "workers": args.workers},
        "metrics": [sequential_metrics, ray_metrics],
        "duplicate_delivery": duplicate,
        "sequential_observations": sequential,
        "ray_observations": parallel,
    }
    (run_dir / "results.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    (run_dir / "RESULTS.md").write_text(comparison_markdown(document["metrics"], duplicate, parallel), encoding="utf-8")
    (ray_dir / "MATRIX.md").write_text(summary(parallel), encoding="utf-8")
    print(run_dir / "RESULTS.md")


if __name__ == "__main__":
    main()
