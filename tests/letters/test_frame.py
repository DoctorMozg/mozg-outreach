import pytest

from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.pipelines.biz.models import SenderCard

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

    def test_the_privacy_line_follows_the_opt_out(self) -> None:
        frame = LetterFrame.build("email", "neutral", PARTY, "en").with_opt_out(
            "en", "sgon.ai/privacy"
        )
        finished = frame.finish(frame.assemble("Body."))
        assert finished.splitlines()[-5:-3] == [
            "If this isn't relevant to you, just let me know and I won't write again.",
            "How I handle your data: sgon.ai/privacy",
        ]


class TestCard:
    CARD = "Stan Petrov\nFounder, Acme\nacme.example | linkedin.com/in/stan"

    def test_the_card_goes_under_the_sign_off(self) -> None:
        frame = LetterFrame.build("email", "neutral", PARTY, "en").with_opt_out("en")
        frame = frame.model_copy(update={"card": self.CARD})

        finished = frame.finish(frame.assemble("Body."))

        assert finished.endswith(f"Best regards,\nStan\n\n{self.CARD}")
        assert f"{frame.opt_out}\n\nBest regards," in finished

    def test_the_card_reads_like_a_signature(self) -> None:
        card = SenderCard(
            title="Founder",
            company="Acme",
            website="https://www.acme.example/",
            linkedin="https://www.linkedin.com/in/stan/",
            phone="+971 50 000 0000",
        )
        assert card.text("Stan Petrov") == (
            "Stan Petrov\nFounder, Acme\nacme.example | linkedin.com/in/stan | +971 50 000 0000"
        )

    def test_no_card_when_only_the_name_is_known(self) -> None:
        assert SenderCard().text("Stan Petrov") is None


@pytest.mark.parametrize(
    ("channel", "recipient", "salutation"),
    [
        ("email", "Jane Doe", "Hello Jane,"),
        ("dm", "Jane Doe", "Hello Jane,"),
        ("dm", None, "Hello,"),
    ],
)
def test_gulf_readers_never_get_a_casual_hi(
    channel: Channel, recipient: str | None, salutation: str
) -> None:
    party = LetterParty(recipient=recipient, sender="Stan Petrov")
    frame = LetterFrame.build(channel, "warm", party, "en", casual_hi=False)
    assert frame.salutation == salutation
