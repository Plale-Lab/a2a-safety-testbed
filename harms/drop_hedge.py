import re

HEDGE_TERMS = ["preliminary", "possible", "possibly", "unconfirmed", "tentative", "suspected"]

_PATTERN = re.compile(r"\b(?:" + "|".join(HEDGE_TERMS) + r")\b", re.IGNORECASE)


def drop_hedge(text: str) -> str:
    """Strips hedge/uncertainty language from text.

    Simulates classification/inference failure: a tentative claim hardens
    into unqualified fact through ordinary relay/summarization, no external
    tampering needed -- unlike strip_provenance, this models agent behavior,
    not an adversarial intervention in transit.
    """
    stripped = _PATTERN.sub("", text)
    return re.sub(r"\s+", " ", stripped).strip(" :,")
