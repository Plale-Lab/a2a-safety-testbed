import logging

from google.protobuf.json_format import MessageToDict

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Part, Task, TaskState, TaskStatus

logger = logging.getLogger("agent_b")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


class EchoAgentExecutor(AgentExecutor):
    """Acknowledges the incoming message, echoes its text and provenance back."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        logger.info("task %s: submitted", context.task_id)
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )

        logger.info("task %s: working", context.task_id)
        await updater.start_work()

        user_text = context.get_user_input()
        incoming_metadata = MessageToDict(context.message.metadata) if context.message else {}
        provenance = incoming_metadata.get("provenance")
        logger.info("task %s: received provenance=%s", context.task_id, provenance)

        reply_text = f"echo: {user_text} | provenance: {provenance if provenance else 'MISSING'}"
        reply = updater.new_agent_message([Part(text=reply_text)])

        logger.info("task %s: completed", context.task_id)
        await updater.complete(message=reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(state=TaskState.TASK_STATE_CANCELED)
