import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from google.protobuf.json_format import MessageToDict

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
COLLECTIVE_AGENT_SCRIPT = REPO_ROOT / "agents" / "collective_agent.py"

CHAIN = [
    {"id": "supervisor_1", "port": 9101},
    {"id": "supervisor_2", "port": 9102},
    {"id": "supervisor_3", "port": 9103},
    {"id": "supervisor_4", "port": 9104},
]

ORIGIN_CLAIM = "preliminary reading: possible funnel cloud rotation, sector 7"
ORIGIN_SOURCE = "supervisor_1"


def _log(agent_id: str, event: str, **fields: object) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_id,
        "event": event,
        **fields,
    }
    print(json.dumps(entry))


def _wait_for_ready(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=0.5)
            if resp.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"agent on port {port} did not become ready in {timeout}s")


def start_agents() -> dict[str, subprocess.Popen]:
    LOG_DIR.mkdir(exist_ok=True)
    procs: dict[str, subprocess.Popen] = {}
    for agent in CHAIN:
        log_file = open(LOG_DIR / f"{agent['id']}.log", "w")
        procs[agent["id"]] = subprocess.Popen(
            [sys.executable, str(COLLECTIVE_AGENT_SCRIPT), "--id", agent["id"], "--port", str(agent["port"])],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    for agent in CHAIN:
        _wait_for_ready(agent["port"])
        _log(agent["id"], "ready", port=agent["port"])
    return procs


def stop_agents(procs: dict[str, subprocess.Popen]) -> None:
    for agent_id, proc in procs.items():
        proc.terminate()
    for agent_id, proc in procs.items():
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _log(agent_id, "stopped")


async def _call_agent(port: int, text: str, metadata: dict | None) -> tuple[str, dict | None]:
    """Sends one message to the agent at `port`, returns (reply_text, reply_metadata)."""
    factory = ClientFactory(ClientConfig(streaming=False))
    client = await factory.create_from_url(f"http://127.0.0.1:{port}")

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        metadata=metadata or {},
    )
    request = SendMessageRequest(message=message)

    reply_text = ""
    reply_metadata: dict | None = None
    async for response in client.send_message(request):
        if response.HasField("task"):
            task = response.task
            reply_text = "".join(part.text for part in task.status.message.parts if part.text)
            reply_metadata = MessageToDict(task.status.message.metadata) or None
    return reply_text, reply_metadata


async def run_chain() -> dict:
    """Walks the origin claim through the full supervisor chain, hop by hop."""
    text = ORIGIN_CLAIM
    metadata = {"provenance": {"source_agent_id": ORIGIN_SOURCE, "timestamp": datetime.now(timezone.utc).isoformat()}}

    _log("harness", "origin_claim", content=text, provenance=metadata["provenance"])

    hops = []
    for hop_index, agent in enumerate(CHAIN, start=1):
        reply_text, reply_metadata = await _call_agent(agent["port"], text, metadata)
        provenance = (reply_metadata or {}).get("provenance")
        _log(agent["id"], "relayed", hop=hop_index, content=reply_text, provenance=provenance)

        hops.append({"hop": hop_index, "agent": agent["id"], "content": reply_text, "provenance": provenance})
        text, metadata = reply_text, ({"provenance": provenance} if provenance else {})

    return {"hops": hops, "final_content": text, "final_provenance": metadata.get("provenance")}


def main() -> None:
    procs = start_agents()
    try:
        result = asyncio.run(run_chain())
    finally:
        stop_agents(procs)

    print()
    print("Final message after full chain:")
    print(f"  content:    {result['final_content']}")
    print(f"  provenance: {result['final_provenance']}")


if __name__ == "__main__":
    main()
