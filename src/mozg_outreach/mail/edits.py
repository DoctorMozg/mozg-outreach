"""How much of a draft the user changed before sending: the main signal of draft quality."""

from difflib import SequenceMatcher


def edited_share(draft: str, sent: str) -> float:
    """0.0 when sent as drafted, toward 1.0 as more words changed; whitespace doesn't count."""
    before, after = draft.split(), sent.split()
    if not before and not after:
        return 0.0
    return round(1 - SequenceMatcher(None, before, after, autojunk=False).ratio(), 2)
