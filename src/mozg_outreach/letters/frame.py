"""The fixed parts of a letter: Python writes the salutation and sign-off, never the model."""

from pydantic import BaseModel, Field

from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel

SALUTATIONS: dict[Tone, str] = {"warm": "Hi {first},", "neutral": "Hello {first},"}
CLOSINGS: dict[Tone, str] = {
    "warm": "Thanks,",
    "neutral": "Best regards,",
    "formal": "Sincerely,",
}


class LetterParty(BaseModel):
    recipient: str = Field(min_length=1, description="Recipient's full name.")
    sender: str = Field(min_length=1, description="Sender's name from the project settings.")


class LetterFrame(BaseModel):
    salutation: str
    closing: str
    signature: str

    @classmethod
    def build(cls, channel: Channel, tone: Tone, party: LetterParty) -> "LetterFrame":
        first = first_name(party.recipient)
        if channel == "dm":
            # A DM is casual whatever the tone: a formal close reads like a form letter.
            return cls(
                salutation=f"Hi {first},", closing="Thanks,", signature=first_name(party.sender)
            )
        if channel == "proposal":
            # Proposals go into a marketplace thread, where "Dear" reads stiff.
            return cls(
                salutation=f"Hi {first},",
                closing=CLOSINGS[tone],
                signature=first_name(party.sender),
            )
        if tone == "formal" or channel == "cover_letter":
            # Cover letters open with "Dear" whatever the tone; a full name avoids guessing "Ms.".
            salutation = f"Dear {' '.join(party.recipient.split())},"
        else:
            salutation = SALUTATIONS[tone].format(first=first)
        return cls(
            salutation=salutation, closing=CLOSINGS[tone], signature=first_name(party.sender)
        )

    def assemble(self, body: str) -> str:
        paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
        return "\n\n".join([self.salutation, *paragraphs, f"{self.closing}\n{self.signature}"])


def first_name(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        raise ValueError(f"no name in {full_name!r}")
    return parts[0]
