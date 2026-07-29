import asyncio
import uuid

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest, TaskState

AGENT_B_URL = "http://127.0.0.1:9002"


def _state_name(state: int) -> str:
    return TaskState.Name(state)


async def send_one_message() -> None:
    factory = ClientFactory(ClientConfig(streaming=False))
    client = await factory.create_from_url(AGENT_B_URL)

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text="hello from agent_a")],
    )
    request = SendMessageRequest(message=message)

    async for response in client.send_message(request):
        if response.HasField("task"):
            task = response.task
            print(f"[agent_b] task {task.id} status: {_state_name(task.status.state)}")
        elif response.HasField("message"):
            reply = response.message
            text = "".join(part.text for part in reply.parts if part.text)
            print(f"[agent_b] message reply: {text}")
        elif response.HasField("status_update"):
            update = response.status_update
            print(f"[agent_b] task {update.task_id} status: {_state_name(update.status.state)}")
        elif response.HasField("artifact_update"):
            print("[agent_b] artifact update received")


if __name__ == "__main__":
    asyncio.run(send_one_message())
