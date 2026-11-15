import pytest

from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.models import Angle, Hook, LetterBrief, LetterRecipient
from mozg_outreach.naturalize.models import WordBudget


@pytest.fixture
def email_brief() -> LetterBrief:
    return LetterBrief(
        channel="email",
        language="en",
        recipient=LetterRecipient(name="Jane Example", title="CTO", company="Katana"),
        generic_inbox=False,
        word_budget=WordBudget(min=20, max=60),
        subject_max_chars=55,
        angles=[Angle(name="cost", pain_point="cloud spend", value_prop="cut AWS bills")],
        hooks=[
            Hook(
                signal="Katana raised a 2024 Series B of $20M",
                source="news",
                source_url="https://katanamrp.com/news",
            )
        ],
        verified_entities=["Katana", "AWS", "Series B", "$20M", "2024"],
        reader_style=None,
        sender_voice="plain, direct",
    )


@pytest.fixture
def email_frame() -> LetterFrame:
    return LetterFrame.build(
        "email", "warm", LetterParty(recipient="Jane Example", sender="Stan Petrov"), "en"
    )
