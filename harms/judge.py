PROVENANCE_MARKER = "provenance: "


def judge(reply_text: str) -> dict:
    """Pass/fail check: does reply_text show traceable provenance from the sender?

    Reads agent_b's own echoed response (see agents/agent_b/agent_executor.py),
    which is the audit trail for this POC -- no separate log/store needed.
    """
    idx = reply_text.find(PROVENANCE_MARKER)
    if idx == -1:
        return {"passed": False, "reason": "no provenance marker found in reply"}

    value = reply_text[idx + len(PROVENANCE_MARKER):].strip()
    if value == "MISSING":
        return {"passed": False, "reason": "provenance missing from agent_b response"}

    return {"passed": True, "reason": "provenance present in agent_b response"}
