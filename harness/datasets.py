"""Paired stock-A2A versus signed-provenance A2A dataset evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from safety.attribution import build_envelope, canonical_json, load_json, mutate  # noqa: E402
from safety.workset import counts  # noqa: E402

SCENARIOS = ("clean", "strip-provenance", "content-substitution", "forged-identity", "unknown-key", "authorized-substitution")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"dataset index on {port} did not become ready")


def scenario_payload(scenario: str, run_id: str, message_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    descriptors = load_json(ROOT / "fixtures" / "datasets.json")
    identity = load_json(ROOT / "fixtures" / "trust_registry.json")["dataset_source"]
    signing_key = load_json(ROOT / "fixtures" / "source_signing_keys.json")["dataset_source"]
    envelope = build_envelope(descriptors, source_agent_id="dataset_source", principal=identity["principal"], key_id=identity["key_id"], private_seed_hex=signing_key["private_seed_hex"], run_id=run_id, message_id=message_id)
    if scenario == "strip-provenance":
        return descriptors, None
    if scenario == "content-substitution":
        descriptors = mutate(descriptors)
        descriptors[0]["checksum"] = "sha256:substituted-after-signing"
    elif scenario == "forged-identity":
        envelope = mutate(envelope)
        envelope["source_agent_id"] = "index_operator"
        envelope["principal"] = "example-index-operator"
        envelope["key_id"] = "index-operator-2026-01"
    elif scenario == "unknown-key":
        envelope = build_envelope(descriptors, source_agent_id="unregistered_source", principal="unregistered-lab", key_id="rogue-key", private_seed_hex="4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", run_id=run_id, message_id=message_id)
    elif scenario == "authorized-substitution":
        descriptors = mutate(descriptors)
        descriptors[0]["checksum"] = "sha256:authorized-but-wrong"
        envelope = build_envelope(descriptors, source_agent_id="dataset_source", principal=identity["principal"], key_id=identity["key_id"], private_seed_hex=signing_key["private_seed_hex"], run_id=run_id, message_id=message_id)
    return descriptors, envelope


async def send(port: int, scenario: str, run_id: str) -> dict[str, Any]:
    message_id = str(uuid.uuid4())
    descriptors, envelope = scenario_payload(scenario, run_id, message_id)
    payload = {"run_id": run_id, "message_id": message_id, "descriptors": descriptors}
    # This value measures the actual application payload supplied to A2A before SDK framing.
    payload_bytes = len(canonical_json(payload)) + len(canonical_json(envelope)) if envelope else len(canonical_json(payload))
    message = Message(message_id=message_id, role=Role.ROLE_USER, parts=[Part(text=json.dumps(payload, sort_keys=True, separators=(",", ":")))], metadata={"beacon_provenance": envelope} if envelope else {})
    client = await ClientFactory(ClientConfig(streaming=False)).create_from_url(f"http://127.0.0.1:{port}")
    started = time.perf_counter_ns()
    decision: dict[str, Any] | None = None
    async for response in client.send_message(SendMessageRequest(message=message)):
        if response.HasField("task") and response.task.status.message.parts:
            text = "".join(part.text for part in response.task.status.message.parts if part.text)
            if text:
                decision = json.loads(text)
    return {"scenario": scenario, "message_id": message_id, "decision": decision, "request_latency_ms": (time.perf_counter_ns() - started) / 1_000_000, "application_payload_bytes": payload_bytes}


def run_condition(condition: str, scenario: str, repetitions: int, output: Path) -> list[dict[str, Any]]:
    workset = output / f"{condition}-{scenario}.sqlite"
    port = free_port()
    process = subprocess.Popen([sys.executable, str(ROOT / "agents" / "dataset_index.py"), "--port", str(port), "--condition", condition, "--workset", str(workset)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    startup = time.perf_counter_ns()
    try:
        wait_ready(port)
        startup_ms = (time.perf_counter_ns() - startup) / 1_000_000
        asyncio.run(send(port, scenario, "warmup"))
        observations = [asyncio.run(send(port, scenario, f"{condition}-{scenario}-{index}")) for index in range(repetitions)]
        workset_counts = counts(workset)
        for item in observations:
            item.update({"condition": condition, "startup_ms": startup_ms, "workset": workset.name, **workset_counts})
        return observations
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def summary(observations: list[dict[str, Any]]) -> str:
    lines = ["# Dataset attribution evaluation", "", "| Condition | Scenario | accepted / runs | median ms | p95 ms | payload bytes |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for condition in ("stock", "extended"):
        for scenario in SCENARIOS:
            rows = [row for row in observations if row["condition"] == condition and row["scenario"] == scenario]
            latencies = sorted(row["request_latency_ms"] for row in rows)
            accepted = sum(bool(row["decision"] and row["decision"]["accepted"]) for row in rows)
            p95 = latencies[min(len(latencies) - 1, round((len(latencies) - 1) * .95))]
            lines.append(f"| {condition} | {scenario} | {accepted} / {len(rows)} | {statistics.median(latencies):.2f} | {p95:.2f} | {rows[0]['application_payload_bytes']} |")
    lines.extend(["", "`application_payload_bytes` is the canonical application payload before A2A SDK/HTTP framing. Startup timing and raw observations are in `runs.json`."])
    return "\n".join(lines) + "\n"


def validate_observations(observations: list[dict[str, Any]]) -> None:
    """Prevent a report from being emitted as a successful experiment on bad behavior."""
    for observation in observations:
        condition, scenario = observation["condition"], observation["scenario"]
        accepted = bool(observation["decision"] and observation["decision"]["accepted"])
        expected = condition == "stock" or scenario == "clean"
        if accepted != expected:
            raise RuntimeError(f"{condition}/{scenario}: expected accepted={expected}, received {accepted}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=("stock", "extended"))
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--repetitions", type=int, default=30)
    args = parser.parse_args()
    if not args.compare and not (args.condition and args.scenario):
        parser.error("select --compare or both --condition and --scenario")
    run_dir = ROOT / "results" / "datasets" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True)
    conditions = ("stock", "extended") if args.compare else (args.condition,)
    scenarios = SCENARIOS if args.compare else (args.scenario,)
    observations = [item for condition in conditions for scenario in scenarios for item in run_condition(condition, scenario, args.repetitions, run_dir)]
    validate_observations(observations)
    (run_dir / "runs.json").write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
    (run_dir / "RESULTS.md").write_text(summary(observations), encoding="utf-8")
    print(run_dir / "RESULTS.md")


if __name__ == "__main__":
    main()
