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

## Dataset attribution evaluation

`harness/datasets.py` is a paired, deterministic evaluation for BEACON's
attribution work.  A source agent sends five dataset descriptors to a separately
run index agent over A2A. The baseline condition—unmodified A2A without the
provenance-verification extension—accepts the payload as supplied (the harness
retains the raw identifier `stock`). The provenance-enabled condition (raw
identifier `extended`) verifies a signed, versioned provenance envelope and checks
the descriptors against an independently trusted manifest before atomically
persisting them to a SQLite workset.

```bash
source venv/bin/activate
python harness/datasets.py --compare --repetitions 30
```

The comparison covers a clean batch, removed provenance, post-signing content
substitution, forged identity, an unknown key, and an authorized source signing
a descriptor whose checksum conflicts with the trusted manifest.  Each run writes
raw observations, incident evidence, SQLite worksets, and a Markdown report under
`results/datasets/<run-id>/`.  Fixture identities and the manifest are local test
trust roots; this is an application-level experimental A2A extension, not a claim
that Agent Cards themselves establish identity or that a signature establishes a
dataset's truth.

## Local Ray Core experiment

`harness/ray_datasets.py` keeps the attribution code above unchanged and uses
Ray tasks only to schedule the twelve independent condition/scenario cells. It
runs a fresh sequential baseline, runs the same matrix with local Ray workers,
checks that every acceptance outcome agrees, and exercises an application-level
idempotency key with a duplicate delivery.

```bash
source venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python harness/datasets.py --compare --repetitions 1  # sequential smoke test
python harness/ray_datasets.py --repetitions 10 --workers 4
```

Each Ray task owns its A2A server process and SQLite workset. Generated raw data
is written under `results/ray/<UTC timestamp>/`; the compact checked-in snapshot
used by the formal meeting brief is `ray-demo-results.json`. See
[`A2A_PROVENANCE_RESEARCH_BRIEF.md`](A2A_PROVENANCE_RESEARCH_BRIEF.md) for the research
question, design, results, interpretation, and discussion prompts. This is
deliberately Ray Core on one machine—there is no Ray Serve, Ray Data, Docker,
Kubernetes, or multi-node scalability claim.

## Three-agent provenance-lineage study

`harness/lineage.py` evaluates a clean retrieve → filter → summarize chain
against missing-middle-record, post-signing transformation mutation, and signed
record reordering attacks. In the provenance-enabled condition,
each agent appends an Ed25519-signed transformation record that binds its input,
output, identity, sequence, and prior record digest. The baseline condition is
unmodified pass-through without lineage verification.

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
python harness/lineage.py --repetitions 10 --workers 4
```

The command writes structured observations and SQLite worksets under
`results/lineage/<run-id>/` and refreshes `lineage-demo-results.json`. Ray is
used only to schedule independent local trials. The provenance-lineage design,
verification results, and limitations are documented in
[`A2A_PROVENANCE_RESEARCH_BRIEF.md`](A2A_PROVENANCE_RESEARCH_BRIEF.md).

## Run agent_a standalone

```bash
source venv/bin/activate
python agents/agent_a/__main__.py
# in another terminal:
curl http://127.0.0.1:9001/.well-known/agent-card.json
```
