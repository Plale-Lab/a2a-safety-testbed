import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Part, Task, TaskState, TaskStatus

logger = logging.getLogger("agent_b")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


class EchoAgentExecutor(AgentExecutor):
    """Acknowledges the incoming message and echoes its text back."""

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
        reply = updater.new_agent_message([Part(text=f"echo: {user_text}")])

        logger.info("task %s: completed", context.task_id)
        await updater.complete(message=reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(state=TaskState.TASK_STATE_CANCELED)
