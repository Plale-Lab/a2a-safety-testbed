# Build notes / checkpoints

Tracks progress against `patra-agent-cards/a2a-testbed-10session-poc-plan.md`
(Sessions 1-2, VM/SSH, are skipped — building locally). Update this after
every checkpoint so work can resume cleanly if cut off mid-build.

## SDK reality check (important, read before writing more agent code)

The installed `a2a-sdk` (1.1.2) is **not** the lightweight JSON-RPC/Pydantic
SDK shown in older `a2a-samples` tutorials floating around online — its
`a2a.types` are **protobuf-generated messages** (`a2a_pb2`), and the server
framework is considerably fuller: `TaskUpdater`, `TaskState.TASK_STATE_*`
enums, `RequestContext`, event queues, etc. Field names on `AgentCard` /
`AgentSkill` / `Message` still match what older docs show (protobuf
constructors accept the same kwargs), but always confirm against the
installed package before assuming an API shape:
```bash
python3 -c "from a2a.types.a2a_pb2 import Message; print([f.name for f in Message.DESCRIPTOR.fields])"
```
Key facts learned so far:
- Agent card route: `/.well-known/agent-card.json` (`a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH`).
- Server wiring: `DefaultRequestHandler(agent_executor, task_store, agent_card)` + `create_agent_card_routes()` + `create_jsonrpc_routes(handler, "/")`, mounted on a Starlette app, run with uvicorn.
- `AgentExecutor` subclasses implement `execute(context, event_queue)` and `cancel(...)`; use `TaskUpdater(event_queue, context.task_id, context.context_id)` to publish status/artifacts, `updater.complete(message=...)` to finish.
- `Message` fields: `message_id, context_id, task_id, role, parts, metadata, extensions, reference_task_ids` — `metadata` is the planned home for the Session 6 provenance field.
- `Role` enum: `ROLE_USER`, `ROLE_AGENT`. `Part` has a `text` field for plain text.
- Needed deps beyond `a2a-sdk`+`uvicorn`: `sse-starlette`, `starlette` (the JSON-RPC routes import fails without `sse_starlette` installed).

## Checkpoints

- [x] **Session 3 — Repo scaffold + A2A SDK.** `git init`, venv (python3.11), `requirements.txt` (a2a-sdk, uvicorn, sse-starlette, starlette). Scaffolded `agents/`, `harness/`, `harms/`. Built `agents/agent_a/` (`__main__.py`, `agent_executor.py` — `EchoAgentExecutor`, echoes back `echo: <input text>`). **Verified**: booted on port 9001, `curl http://127.0.0.1:9001/.well-known/agent-card.json` returned valid AgentCard JSON (name, skills, capabilities all present).
- [x] **Session 4 — Two-agent round trip.** `agents/agent_b/` (port 9002), identical pattern to `agent_a`. `harness/run.py` uses `ClientFactory(ClientConfig(streaming=False)).create_from_url(...)` to send one `Message` (role `ROLE_USER`, text "hello from agent_a") to agent_b. **Gotcha found & fixed**: the framework requires the executor to `enqueue_event()` an initial `Task` object *before* any `TaskStatusUpdateEvent` — `TaskUpdater.update_status()`/`.start_work()` alone raises `InvalidAgentResponseError: Agent should enqueue Task before TaskStatusUpdateEvent event`. Fix: executor now does `event_queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id, status=TaskStatus(state=TASK_STATE_SUBMITTED)))` first, then `updater.start_work()`, then `updater.new_agent_message(...)` + `updater.complete(...)`. Applied to both agents' executors, with `logging` calls at each transition. **Verified**: agent_b's log shows `submitted -> working -> completed`; harness client receives final `TASK_STATE_COMPLETED`. Also learned: killing background dev-server processes between test runs must be done by port (`lsof -ti:PORT | xargs kill -9`), not `jobs -p` — each Bash tool invocation is a new shell, so `jobs` from a prior command isn't visible.
- [x] **Session 5 — Harness script.** `harness/run.py` now: starts both agents as subprocesses (stdout/stderr to `logs/<agent>.log`, gitignored), polls each agent's agent-card endpoint until ready (`httpx`, 10s timeout), sends one message to agent_b, prints structured JSON log lines to stdout (`timestamp`, `agent`, `event`, plus `content`/`state`/`task_id` as relevant), then terminates both subprocesses. **Verified**: `python harness/run.py` run standalone (no manual agent startup) exits 0 and prints the full sequence: both agents ready -> send_message -> task_status COMPLETED with the echoed reply text -> both stopped. Learned: the final `Task` response's echoed message text lives at `task.status.message.parts[].text` (the message passed to `updater.complete(message=...)`), not as a separate top-level `message` response — needed to dig it out to get `content` into the structured log.
- [x] **Session 6 — Provenance field.** `harness/run.py` attaches `metadata={"provenance": {"source_agent_id": "agent_a", "timestamp": <iso>}}` on the outgoing `Message` (`Message.metadata` is a `google.protobuf.Struct` — accepts a plain nested Python dict directly in the constructor, converts back via `google.protobuf.json_format.MessageToDict`). `agent_b`'s executor reads `context.message.metadata`, extracts `provenance`, logs it server-side, and folds it into the echoed reply text (`"echo: <text> | provenance: <dict or 'MISSING'>"`) so both the server log and the harness's structured `task_status` log show it. **Verified**: full harness run shows provenance dict in the `send_message` log entry and the identical dict echoed back in `task_status`'s `content` field and in `agent_b`'s own log line.
- [ ] Session 7 — harms/strip_provenance.py, toggle in harness.
- [ ] Session 8 — harms/judge.py pass/fail.
- [ ] Session 9 — before/after comparison command.
