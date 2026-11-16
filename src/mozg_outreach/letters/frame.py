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
    inbox: str = Field(description="To a company's shared inbox, with no person to greet.")
    closings: dict[Tone, str]
    opt_out: str = Field(description="One line offering to stop writing, where rules ask for it.")


WORDINGS: dict[str, Wording] = {
    "en": Wording(
        dear="Dear {full},",
        hello="Hello {first},",
        hi="Hi {first},",
        team="Dear Hiring Team,",
        there="Hi there,",
        inbox="Hello,",
        closings={"warm": "Thanks,", "neutral": "Best regards,", "formal": "Sincerely,"},
        opt_out="If this isn't relevant to you, just let me know and I won't write again.",
    ),
    "ru": Wording(
        dear="Здравствуйте, {first}!",
        hello="Здравствуйте, {first}!",
        hi="Добрый день, {first}!",
        team="Здравствуйте!",
        there="Добрый день!",
        inbox="Здравствуйте!",
        closings={"warm": "Спасибо,", "neutral": "С уважением,", "formal": "С уважением,"},
        opt_out="Если это вам неактуально, просто дайте знать, и я больше не буду писать.",
    ),
    "uk": Wording(
        dear="Добрий день, {first}!",
        hello="Добрий день, {first}!",
        hi="Добрий день, {first}!",
        team="Добрий день!",
        there="Добрий день!",
        inbox="Добрий день!",
        closings={"warm": "Дякую,", "neutral": "З повагою,", "formal": "З повагою,"},
        opt_out="Якщо це для вас неактуально, просто дайте знати, і я більше не писатиму.",
    ),
    # German, French and Spanish avoid Herr/Frau, Monsieur/Madame and Estimado/a: no gender guess.
    "de": Wording(
        dear="Guten Tag {full},",
        hello="Hallo {first},",
        hi="Hallo {first},",
        team="Sehr geehrte Damen und Herren,",
        there="Hallo,",
        inbox="Guten Tag,",
        closings={
            "warm": "Danke,",
            "neutral": "Viele Grüße,",
            "formal": "Mit freundlichen Grüßen,",
        },
        opt_out=(
            "Falls das für Sie nicht relevant ist, sagen Sie einfach Bescheid, "
            "dann schreibe ich nicht wieder."
        ),
    ),
    "fr": Wording(
        dear="Bonjour {full},",
        hello="Bonjour {first},",
        hi="Bonjour {first},",
        team="Madame, Monsieur,",
        there="Bonjour,",
        inbox="Bonjour,",
        closings={"warm": "Merci,", "neutral": "Bien cordialement,", "formal": "Cordialement,"},
        opt_out=(
            "Si ce n'est pas pertinent pour vous, dites-le-moi simplement "
            "et je ne vous écrirai plus."
        ),
    ),
    "es": Wording(
        dear="Buenos días, {full}:",
        hello="Hola, {first}:",
        hi="Hola, {first}:",
        team="Estimado equipo de selección:",
        there="Hola:",
        inbox="Buenos días:",
        closings={"warm": "Gracias,", "neutral": "Un saludo,", "formal": "Atentamente,"},
        opt_out="Si no le resulta relevante, dígamelo y no volveré a escribirle.",
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
    opt_out: str | None = Field(
        default=None, description="Added above the closing, after naturalizing."
    )

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
            salutation = words.team if channel == "cover_letter" else words.inbox
        elif tone == "formal" or channel == "cover_letter":
            # Cover letters are formal whatever the tone; a full name avoids guessing "Ms.".
            salutation = words.dear.format(
                full=" ".join(party.recipient.split()), first=first_name(party.recipient)
            )
        else:
            template = words.hi if tone == "warm" else words.hello
            salutation = template.format(first=first_name(party.recipient))
        return cls(salutation=salutation, closing=closing, signature=signature)

    def with_opt_out(self, language: str) -> "LetterFrame":
        return self.model_copy(update={"opt_out": WORDINGS[letter_language(language)].opt_out})

    def assemble(self, body: str) -> str:
        paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
        return "\n\n".join([self.salutation, *paragraphs, self._ending()])

    def finish(self, letter: str) -> str:
        """Adds the opt-out line; it stays out of the word budget and the naturalizer's reach."""
        if self.opt_out is None:
            return letter
        ending = self._ending()
        # Naturalize validation keeps the sign-off lines verbatim; spacing around them may vary.
        head, found, tail = letter.rstrip().rpartition(f"\n{ending}")
        if not found or tail:
            raise ValueError(f"letter does not end with {ending!r}")
        return f"{head.rstrip()}\n\n{self.opt_out}\n\n{ending}"

    def _ending(self) -> str:
        return f"{self.closing}\n{self.signature}"


def _hi(words: Wording, party: LetterParty) -> str:
    if party.recipient is None:
        return words.there
    return words.hi.format(first=first_name(party.recipient))


def first_name(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        raise ValueError(f"no name in {full_name!r}")
    return parts[0]
