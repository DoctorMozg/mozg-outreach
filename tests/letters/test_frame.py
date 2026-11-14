import pytest

from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel

PARTY = LetterParty(recipient="Jane  Example", sender="Stan Petrov")


@pytest.mark.parametrize(
    ("channel", "tone", "salutation", "closing"),
    [
        ("email", "warm", "Hi Jane,", "Thanks,"),
        ("email", "neutral", "Hello Jane,", "Best regards,"),
        ("email", "formal", "Dear Jane Example,", "Sincerely,"),
        ("dm", "formal", "Hi Jane,", "Thanks,"),
    ],
)
def test_frame_follows_channel_and_tone(
    channel: Channel, tone: Tone, salutation: str, closing: str
) -> None:
    frame = LetterFrame.build(channel, tone, PARTY)
    assert (frame.salutation, frame.closing, frame.signature) == (salutation, closing, "Stan")


def test_assemble_puts_body_between_salutation_and_signature() -> None:
    frame = LetterFrame.build("email", "warm", PARTY)
    text = frame.assemble("\n\nFirst paragraph.\n\n\n\nSecond paragraph.\n")
    assert text == "Hi Jane,\n\nFirst paragraph.\n\nSecond paragraph.\n\nThanks,\nStan"


def test_blank_sender_is_rejected() -> None:
    with pytest.raises(ValueError, match="no name"):
        LetterFrame.build("email", "warm", LetterParty(recipient="Jane", sender="   "))
