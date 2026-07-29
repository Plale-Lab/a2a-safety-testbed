# a2a-safety-testbed

A minimal two-agent A2A (Agent-to-Agent) proof of concept: one agent sends a
message to another, a toggleable "harm injector" strips a provenance field in
transit, and a judge script detects the tampering. Built to support Beth
Plale's Schmidt Sciences multi-agent-safety proposal (attribution/provenance
aim).

See `NOTES.md` for build progress and `plale_lab/patra-agent-cards/a2a-testbed-10session-poc-plan.md`
for the original session-by-session plan this follows.

## Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run agent_a

```bash
source venv/bin/activate
python agents/agent_a/__main__.py
# in another terminal:
curl http://127.0.0.1:9001/.well-known/agent-card.json
```
