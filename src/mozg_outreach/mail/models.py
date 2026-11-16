import os
from datetime import datetime
from email.utils import parseaddr
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, SecretStr

ADDRESS_VAR = "MOZG_OUTREACH_GMAIL_ADDRESS"
PASSWORD_VAR = "MOZG_OUTREACH_GMAIL_APP_PASSWORD"


class MailError(RuntimeError):
    """Gmail refused a login or an IMAP command."""


class MailConfigError(ValueError):
    """The Gmail environment variables are half set or malformed."""


def _address(value: str) -> str:
    found = parseaddr(value)[1].strip().lower()
    if "@" not in found or found != value.strip().lower():
        raise ValueError(f"not an email address: {value!r}")
    return found


type Address = Annotated[str, AfterValidator(_address)]


class GmailSettings(BaseModel):
    address: Address
    # Google shows app passwords in groups of four; the spaces are not part of it.
    app_password: SecretStr

    @classmethod
    def from_env(cls) -> "GmailSettings | None":
        """None when Gmail isn't set up; both variables or neither."""
        address = os.environ.get(ADDRESS_VAR, "").strip()
        password = os.environ.get(PASSWORD_VAR, "").replace(" ", "").strip()
        if not address and not password:
            return None
        if not address or not password:
            missing = ADDRESS_VAR if not address else PASSWORD_VAR
            raise MailConfigError(f"environment variable {missing} is not set")
        try:
            return cls(address=address, app_password=SecretStr(password))
        except ValueError as exc:
            raise MailConfigError(f"{ADDRESS_VAR}: {exc}") from exc


class OutgoingDraft(BaseModel):
    to: Address
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    sender_name: str | None = None
    in_reply_to: str | None = Field(
        default=None, description="The Message-ID of the sent letter this one answers."
    )


class PushedDraft(BaseModel):
    """Gmail's ids for a draft: the message changes when edited, the thread stays."""

    message_id: int
    thread_id: int


class ThreadMessage(BaseModel):
    message_id: int
    sender: str = Field(description="The From address, lower case.")
    draft: bool
    received_at: datetime
    header_id: str | None = Field(default=None, description="The Message-ID header.")
    subject: str = ""
