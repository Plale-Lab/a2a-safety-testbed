import argparse

import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)


class RelayAgentExecutor(AgentExecutor):
    """Faithfully forwards whatever text + provenance metadata it receives.

    Stands in for one supervisor in the Weather Warning Agent Swarm's
    peer-to-peer coordination chain (see proposal Use Cases doc): a
    supervisor relays a sensor reading toward the next supervisor unchanged
    unless something (harness-side harm injection, or a lossy-relay mode
    added in a later checkpoint) alters it along the way.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        await updater.start_work()

        user_text = context.get_user_input()
        incoming_metadata = MessageToDict(context.message.metadata) if context.message else {}
        provenance = incoming_metadata.get("provenance")

        reply_metadata = {"provenance": provenance} if provenance else None
        reply = updater.new_agent_message([Part(text=user_text)], metadata=reply_metadata)
        await updater.complete(message=reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(state=TaskState.TASK_STATE_CANCELED)


def build_app(agent_id: str, port: int) -> Starlette:
    skill = AgentSkill(
        id="relay",
        name="Relay",
        description="Forwards a sensor-reading claim toward the next supervisor in the chain.",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["a2a-safety-testbed", "collective"],
        examples=["preliminary reading: possible funnel cloud rotation, sector 7"],
    )

    agent_card = AgentCard(
        name=agent_id,
        description=f"Weather-warning-swarm supervisor stand-in ({agent_id}) in the collective harms testbed.",
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"http://127.0.0.1:{port}/",
                protocol_version="1.0",
            )
        ],
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=RelayAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))
    return Starlette(routes=routes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Agent ID, e.g. supervisor_1")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    app = build_app(args.id, args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port)
