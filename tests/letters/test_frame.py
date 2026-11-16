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
        ("email", UNNAMED, "en", "Hello,", "Best regards,"),
        ("proposal", UNNAMED, "uk", "Добрий день!", "З повагою,"),
        ("cover_letter", UNNAMED, "ja", "Dear Hiring Team,", "Best regards,"),
    ],
    ids=[
        "ru-named",
        "ru-team",
        "en-team",
        "de-named",
        "en-gig",
        "en-inbox",
        "uk-gig",
        "unframed-language",
    ],
)
def test_frame_is_written_in_the_letter_language(
    channel: Channel, party: LetterParty, language: str, salutation: str, closing: str
) -> None:
    frame = LetterFrame.build(channel, "neutral", party, language)
    assert (frame.salutation, frame.closing) == (salutation, closing)


class TestOptOut:
    def test_finish_puts_the_line_above_the_sign_off(self) -> None:
        frame = LetterFrame.build("email", "warm", PARTY, "de").with_opt_out("de")
        letter = frame.assemble("Erster Absatz.")
        # The naturalizer may leave a single newline before the closing.
        squeezed = letter.replace("\n\nDanke,", "\nDanke,")

        finished = frame.finish(squeezed)

        assert finished.splitlines()[-4:] == [frame.opt_out, "", "Danke,", "Stan"]
        assert finished.startswith("Hallo Jane,\n\nErster Absatz.\n\n")

    def test_without_opt_out_the_letter_is_unchanged(self) -> None:
        frame = LetterFrame.build("email", "warm", PARTY, "en")
        letter = frame.assemble("Body.")
        assert frame.finish(letter) == letter

    def test_a_letter_without_the_sign_off_is_an_error(self) -> None:
        frame = LetterFrame.build("email", "warm", PARTY, "en").with_opt_out("en")
        with pytest.raises(ValueError, match="does not end with"):
            frame.finish("Hi Jane,\n\nBody.\n\nCheers,\nStan")
