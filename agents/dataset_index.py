"""A separately operated A2A index agent for the dataset attribution evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, Task, TaskState, TaskStatus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from safety.attribution import load_json, verify_envelope  # noqa: E402
from safety.workset import accept, incident, initialize  # noqa: E402


class DatasetIndexExecutor(AgentExecutor):
    def __init__(self, condition: str, workset_path: Path) -> None:
        self.condition = condition
        self.workset_path = workset_path
        self.trust_registry = load_json(ROOT / "fixtures" / "trust_registry.json")
        self.manifest = load_json(ROOT / "fixtures" / "trusted_manifest.json")
        initialize(workset_path)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await event_queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)))
        await updater.start_work()
        payload = json.loads(context.get_user_input())
        metadata = MessageToDict(context.message.metadata) if context.message else {}
        envelope = metadata.get("beacon_provenance")
        message_id = payload["message_id"]
        descriptors = payload["descriptors"]
        if self.condition == "stock":
            decision = {"accepted": True, "reason": "stock condition does not enforce provenance", "incident_code": None}
            accept(self.workset_path, message_id, envelope.get("source_agent_id") if envelope else None, envelope, descriptors)
        else:
            verdict = verify_envelope(envelope, descriptors, self.trust_registry, self.manifest, expected_run_id=payload["run_id"], expected_message_id=message_id)
            decision = {"accepted": verdict.accepted, "reason": verdict.reason, "incident_code": verdict.incident_code, "authenticated_source": verdict.authenticated_source, "claimed_source": verdict.claimed_source}
            if verdict.accepted:
                accept(self.workset_path, message_id, verdict.authenticated_source, envelope, descriptors)
            else:
                incident(self.workset_path, message_id, verdict.incident_code or "rejected", decision)
        await updater.complete(message=updater.new_agent_message([Part(text=json.dumps(decision, sort_keys=True))]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(state=TaskState.TASK_STATE_CANCELED)


def app(port: int, condition: str, workset: Path) -> Starlette:
    skill = AgentSkill(id="dataset-index", name="Dataset index", description="Registers dataset descriptors for the attribution experiment.", input_modes=["text/plain"], output_modes=["text/plain"], tags=["beacon", "attribution"])
    card = AgentCard(name="Dataset index agent", description="A2A dataset index evaluation peer.", version="0.1.0", default_input_modes=["text/plain"], default_output_modes=["text/plain"], capabilities=AgentCapabilities(streaming=False), supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/", protocol_version="1.0")], skills=[skill])
    handler = DefaultRequestHandler(agent_executor=DatasetIndexExecutor(condition, workset), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--condition", choices=("stock", "extended"), required=True)
    parser.add_argument("--workset", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(app(args.port, args.condition, args.workset), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
