import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest, TaskState

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"

AGENTS = {
    "agent_a": {"port": 9001, "script": REPO_ROOT / "agents" / "agent_a" / "__main__.py"},
    "agent_b": {"port": 9002, "script": REPO_ROOT / "agents" / "agent_b" / "__main__.py"},
}


def _state_name(state: int) -> str:
    return TaskState.Name(state)


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
    for agent_id, cfg in AGENTS.items():
        log_file = open(LOG_DIR / f"{agent_id}.log", "w")
        procs[agent_id] = subprocess.Popen(
            [sys.executable, str(cfg["script"])],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    for agent_id, cfg in AGENTS.items():
        _wait_for_ready(cfg["port"])
        _log(agent_id, "ready", port=cfg["port"])
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


async def send_one_message(content: str = "hello from agent_a") -> None:
    port = AGENTS["agent_b"]["port"]
    factory = ClientFactory(ClientConfig(streaming=False))
    client = await factory.create_from_url(f"http://127.0.0.1:{port}")

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text=content)],
    )
    _log("agent_a", "send_message", content=content)
    request = SendMessageRequest(message=message)

    async for response in client.send_message(request):
        if response.HasField("task"):
            task = response.task
            reply_text = "".join(
                part.text for part in task.status.message.parts if part.text
            )
            _log(
                "agent_b",
                "task_status",
                task_id=task.id,
                state=_state_name(task.status.state),
                content=reply_text or None,
            )
        elif response.HasField("message"):
            reply = response.message
            text = "".join(part.text for part in reply.parts if part.text)
            _log("agent_b", "message_reply", content=text)
        elif response.HasField("status_update"):
            update = response.status_update
            _log("agent_b", "task_status", task_id=update.task_id, state=_state_name(update.status.state))
        elif response.HasField("artifact_update"):
            _log("agent_b", "artifact_update")


def main() -> None:
    procs = start_agents()
    try:
        asyncio.run(send_one_message())
    finally:
        stop_agents(procs)


if __name__ == "__main__":
    main()
