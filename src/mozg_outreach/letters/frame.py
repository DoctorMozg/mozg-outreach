"""The fixed parts of a letter: Python writes the salutation and sign-off, never the model."""

from pydantic import BaseModel, Field

from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel


class Wording(BaseModel):
    """One language's salutations and closings; `{first}` and `{full}` are the reader's names."""

    dear: str = Field(description="Formal, to a named reader.")
    hello: str
    hi: str = Field(description="Casual, for DMs and marketplace threads.")
    team: str = Field(description="Formal, to no named reader: a hiring team.")
    there: str = Field(description="Casual, to no named reader: a gig's client.")
    closings: dict[Tone, str]


WORDINGS: dict[str, Wording] = {
    "en": Wording(
        dear="Dear {full},",
        hello="Hello {first},",
        hi="Hi {first},",
        team="Dear Hiring Team,",
        there="Hi there,",
        closings={"warm": "Thanks,", "neutral": "Best regards,", "formal": "Sincerely,"},
    ),
    "ru": Wording(
        dear="Здравствуйте, {first}!",
        hello="Здравствуйте, {first}!",
        hi="Добрый день, {first}!",
        team="Здравствуйте!",
        there="Добрый день!",
        closings={"warm": "Спасибо,", "neutral": "С уважением,", "formal": "С уважением,"},
    ),
    "uk": Wording(
        dear="Добрий день, {first}!",
        hello="Добрий день, {first}!",
        hi="Добрий день, {first}!",
        team="Добрий день!",
        there="Добрий день!",
        closings={"warm": "Дякую,", "neutral": "З повагою,", "formal": "З повагою,"},
    ),
    # German, French and Spanish avoid Herr/Frau, Monsieur/Madame and Estimado/a: no gender guess.
    "de": Wording(
        dear="Guten Tag {full},",
        hello="Hallo {first},",
        hi="Hallo {first},",
        team="Sehr geehrte Damen und Herren,",
        there="Hallo,",
        closings={
            "warm": "Danke,",
            "neutral": "Viele Grüße,",
            "formal": "Mit freundlichen Grüßen,",
        },
    ),
    "fr": Wording(
        dear="Bonjour {full},",
        hello="Bonjour {first},",
        hi="Bonjour {first},",
        team="Madame, Monsieur,",
        there="Bonjour,",
        closings={"warm": "Merci,", "neutral": "Bien cordialement,", "formal": "Cordialement,"},
    ),
    "es": Wording(
        dear="Buenos días, {full}:",
        hello="Hola, {first}:",
        hi="Hola, {first}:",
        team="Estimado equipo de selección:",
        there="Hola:",
        closings={"warm": "Gracias,", "neutral": "Un saludo,", "formal": "Atentamente,"},
    ),
}


def letter_language(language: str) -> str:
    """The language a letter can be framed in: the one asked for, else English."""
    return language if language in WORDINGS else "en"


class LetterParty(BaseModel):
    recipient: str | None = Field(
        min_length=1, description="Recipient's full name; null when no person is named."
    )
    sender: str = Field(min_length=1, description="Sender's name from the project settings.")


class LetterFrame(BaseModel):
    salutation: str
    closing: str
    signature: str

    @classmethod
    def build(
        cls, channel: Channel, tone: Tone, party: LetterParty, language: str
    ) -> "LetterFrame":
        words = WORDINGS[letter_language(language)]
        closing = words.closings[tone]
        signature = first_name(party.sender)
        if channel == "dm":
            # A DM is casual whatever the tone: a formal close reads like a form letter.
            casual = words.closings["warm"]
            return cls(salutation=_hi(words, party), closing=casual, signature=signature)
        if channel == "proposal":
            # Proposals go into a marketplace thread, where "Dear" reads stiff.
            return cls(salutation=_hi(words, party), closing=closing, signature=signature)
        if party.recipient is None:
            salutation = words.team
        elif tone == "formal" or channel == "cover_letter":
            # Cover letters are formal whatever the tone; a full name avoids guessing "Ms.".
            salutation = words.dear.format(
                full=" ".join(party.recipient.split()), first=first_name(party.recipient)
            )
        else:
            template = words.hi if tone == "warm" else words.hello
            salutation = template.format(first=first_name(party.recipient))
        return cls(salutation=salutation, closing=closing, signature=signature)

    def assemble(self, body: str) -> str:
        paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
        return "\n\n".join([self.salutation, *paragraphs, f"{self.closing}\n{self.signature}"])


def _hi(words: Wording, party: LetterParty) -> str:
    if party.recipient is None:
        return words.there
    return words.hi.format(first=first_name(party.recipient))


def first_name(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        raise ValueError(f"no name in {full_name!r}")
    return parts[0]
