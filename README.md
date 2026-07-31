# a2a-safety-testbed

A minimal, two-agent A2A (Agent-to-Agent) demo: one agent sends another a
message carrying a provenance tag, a toggleable "harm injector" strips that
tag in transit, and a judge script checks whether the receiving agent can
still tell where the message came from.

## What it shows

Two independent A2A servers (`agent_a`, `agent_b`) exchange one message.
The sender attaches a provenance tag (source agent ID + timestamp) in the
message's `metadata` field — the only place A2A currently offers to carry
that kind of information. A harm injector can strip that tag before the
message is sent. The receiving agent echoes back whatever it got, and a
judge script checks: does the reply still carry a traceable origin?

```
Condition     | Provenance | Judge
clean         | present    | PASS
--inject-harm | MISSING    | FAIL
```

**What this maps to in the proposal**: Beth's Schmidt Sciences proposal
("BEACON") argues, in Aim 2 (Attribution), that A2A has no protected, first-class
home for provenance — today it's a bare `metadata` field anyone in the
message path can drop, add, or alter with nothing at the protocol level to
notice. This demo makes that gap concrete rather than abstract: the
provenance tag here is exactly as fragile as Aim 2 says it is — one line of
code (`harms/strip_provenance.py`) removes it cleanly, no protocol
violation, no error, no signal to either agent that anything happened. The
judge script that catches this is a stand-in for what Aim 2 proposes should
be enforced by the protocol itself (persistent per-claim identity binding,
task 1.1; a provenance envelope, task 1.2) — right now, detection only
happens because we built a script to go looking for it after the fact, not
because A2A guarantees it. This demo is evidence for *why* Aim 2's extension
needs to exist, not a working version of the extension itself.

## Quick demo (< 5 min)

```bash
source venv/bin/activate
python harness/run.py --compare
```

Starts both agents, runs the exchange once clean and once with the harm
injector on, prints the comparison table above, then tears the agents down.
See `NOTES.md` for the full session-by-session build log, including the
`a2a-sdk` API gotchas hit along the way.

## Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Other run modes

```bash
python harness/run.py                # single clean run
python harness/run.py --inject-harm  # single run with provenance stripped
```

## Collective demo — multiple agents, multiple harms

The 2-agent demo above is a single hop. `harness/collective.py` extends it
to a **4-agent chain** (`supervisor_1` → `supervisor_2` → `supervisor_3` →
`supervisor_4`), standing in for supervisors in the Weather Warning Agent
Swarm use case from the proposal — relaying a hedged sensor reading
("preliminary reading: possible funnel cloud rotation, sector 7") toward the
agent that would trigger a public take-cover notice. Two harms, modeled
differently on purpose because they're different *kinds* of harm:

- **Loss of traceability** — the same provenance-stripping injector as the
  2-agent demo, generalized to fire at any hop in the chain (external
  tampering in transit).
- **Classification/inference failure** — a relay agent drops hedge language
  ("preliminary", "possible") as it forwards a claim, so a tentative reading
  hardens into unqualified fact through *ordinary relay behavior*, no
  attacker required.

```bash
source venv/bin/activate
python harness/collective.py --compare
```
```
Condition                     | Provenance | Hedge     | Judge
clean                         | present    | preserved | PASS
--inject-provenance-loss-at 2 | MISSING    | preserved | FAIL
--lossy-relay-at 2            | present    | DROPPED   | FAIL
```

Other modes: `python harness/collective.py` (clean run), `--inject-provenance-loss-at HOP`, `--lossy-relay-at HOP` (single-hop, single-harm runs). `agents/agent_a/`, `agents/agent_b/`, and `harness/run.py` are untouched by any of this — the two demos coexist independently.

## Run agent_a standalone

```bash
source venv/bin/activate
python agents/agent_a/__main__.py
# in another terminal:
curl http://127.0.0.1:9001/.well-known/agent-card.json
```
