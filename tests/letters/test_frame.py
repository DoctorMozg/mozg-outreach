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
    frame = LetterFrame.build(channel, tone, PARTY, "en")
    assert (frame.salutation, frame.closing, frame.signature) == (salutation, closing, "Stan")


def test_assemble_puts_body_between_salutation_and_signature() -> None:
    frame = LetterFrame.build("email", "warm", PARTY, "en")
    text = frame.assemble("\n\nFirst paragraph.\n\n\n\nSecond paragraph.\n")
    assert text == "Hi Jane,\n\nFirst paragraph.\n\nSecond paragraph.\n\nThanks,\nStan"


def test_blank_sender_is_rejected() -> None:
    with pytest.raises(ValueError, match="no name"):
        LetterFrame.build("email", "warm", LetterParty(recipient="Jane", sender="   "), "en")


UNNAMED = LetterParty(recipient=None, sender="Stan Petrov")


@pytest.mark.parametrize(
    ("channel", "party", "language", "salutation", "closing"),
    [
        ("cover_letter", PARTY, "ru", "Здравствуйте, Jane!", "С уважением,"),
        ("cover_letter", UNNAMED, "ru", "Здравствуйте!", "С уважением,"),
        ("cover_letter", UNNAMED, "en", "Dear Hiring Team,", "Best regards,"),
        ("cover_letter", PARTY, "de", "Guten Tag Jane Example,", "Viele Grüße,"),
        ("proposal", UNNAMED, "en", "Hi there,", "Best regards,"),
        ("proposal", UNNAMED, "uk", "Добрий день!", "З повагою,"),
        ("cover_letter", UNNAMED, "ja", "Dear Hiring Team,", "Best regards,"),
    ],
    ids=["ru-named", "ru-team", "en-team", "de-named", "en-gig", "uk-gig", "unframed-language"],
)
def test_frame_is_written_in_the_letter_language(
    channel: Channel, party: LetterParty, language: str, salutation: str, closing: str
) -> None:
    frame = LetterFrame.build(channel, "neutral", party, language)
    assert (frame.salutation, frame.closing) == (salutation, closing)
