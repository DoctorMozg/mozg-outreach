import pytest

from mozg_outreach.mail.edits import edited_share


@pytest.mark.parametrize(
    ("sent", "share"),
    [
        ("Hi Ann,\n\nWorth a look at your setup?\n\nThanks,\nStan", 0.0),
        ("Hi Ann,\r\n\r\nWorth  a look at your setup?\r\n\r\nThanks,\r\nStan", 0.0),
        ("Hi Ann,\n\nWorth a quick look at your current setup?\n\nThanks,\nStan", 0.09),
        ("Something else entirely.", 1.0),
    ],
)
def test_edited_share_counts_changed_words(sent: str, share: float) -> None:
    draft = "Hi Ann,\n\nWorth a look at your setup?\n\nThanks,\nStan"
    assert edited_share(draft, sent) == share
