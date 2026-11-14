"""One-time interactive login: python -m mozg_outreach.sources.telegram_login [--qr]"""

import argparse
import asyncio
import getpass
import logging
from datetime import UTC, datetime

import segno
from telethon import TelegramClient
from telethon.errors import SendCodeUnavailableError, SessionPasswordNeededError
from telethon.tl import functions
from telethon.tl.types import auth

from mozg_outreach.sources.telegram import SessionStore, TelegramCredentials

logger = logging.getLogger(__name__)

RESEND = "r"
QR_ATTEMPTS = 6
DELIVERY = {
    auth.SentCodeTypeApp: "to the 'Telegram' service chat on your other logged-in devices",
    auth.SentCodeTypeSms: "by SMS",
    auth.SentCodeTypeCall: "by a phone call that reads it out",
    auth.SentCodeTypeFlashCall: "as the caller number of a flash call",
    auth.SentCodeTypeMissedCall: "as the last digits of a missed call's number",
    auth.SentCodeTypeEmailCode: "to your login email",
    auth.SentCodeTypeFragmentSms: "by SMS through Fragment (anonymous number)",
    auth.SentCodeTypeFirebaseSms: "by SMS",
    auth.SentCodeTypeSmsWord: "by SMS, as a word",
    auth.SentCodeTypeSmsPhrase: "by SMS, as a phrase",
}


def describe(sent: object) -> str:
    """Where Telegram says the code went, so the user knows where to look."""
    if isinstance(sent, auth.SentCodeSuccess):
        return "Telegram logged you in without a code."
    if isinstance(sent, auth.SentCodePaymentRequired):
        return "Telegram wants a payment before it sends an SMS code; log in on a device first."
    if not isinstance(sent, auth.SentCode):
        return f"Unexpected answer from Telegram: {type(sent).__name__}"
    if isinstance(sent.type, auth.SentCodeTypeSetUpEmailRequired):
        return "Telegram requires a login email first: add one in the official app, then retry."
    where = DELIVERY.get(type(sent.type), f"via {type(sent.type).__name__}")
    length = getattr(sent.type, "length", None)
    text = f"Code sent {where}" + (f", {length} digits." if length else ".")
    if sent.next_type is not None:
        method = type(sent.next_type).__name__.removeprefix("CodeType")
        text += f" Type '{RESEND}' to resend via {method}"
        text += f" (wait {sent.timeout}s)." if sent.timeout else "."
    return text


async def sign_in(client: TelegramClient) -> None:
    phone = input("Phone number in international format (+...): ").strip()
    sent = await client.send_code_request(phone)
    print(describe(sent))
    if not isinstance(sent, auth.SentCode) or isinstance(
        sent.type, auth.SentCodeTypeSetUpEmailRequired
    ):
        return
    code_hash, can_resend = sent.phone_code_hash, sent.next_type is not None
    while (code := input("Code: ").strip()) == RESEND:
        if not can_resend:
            print("Telegram offers no other delivery method; the code already sent stays valid.")
            continue
        try:
            sent = await client(functions.auth.ResendCodeRequest(phone, code_hash))
        except SendCodeUnavailableError:
            # Every method was used; the last code sent is still the one to enter.
            print("Telegram has no delivery method left; enter the last code you received.")
            can_resend = False
            continue
        code_hash, can_resend = sent.phone_code_hash, sent.next_type is not None
        print(describe(sent))
    try:
        await client.sign_in(phone, code, phone_code_hash=code_hash)
    except SessionPasswordNeededError:
        await client.sign_in(password=getpass.getpass("Two-step verification password: "))


async def sign_in_with_qr(client: TelegramClient) -> None:
    """Scan from an authorized app: Settings > Devices > Link Desktop Device."""
    qr = await client.qr_login()
    for _ in range(QR_ATTEMPTS):
        segno.make(qr.url, error="l").terminal(compact=True)
        print("Scan this in Telegram: Settings > Devices > Link Desktop Device.")
        try:
            await qr.wait(timeout=max((qr.expires - datetime.now(UTC)).total_seconds(), 5))
            return
        except TimeoutError:
            # The token expires after about 30 seconds; show a fresh one.
            await qr.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Two-step verification password: "))
            return
    print("No scan arrived; run the login again.")


async def login(use_qr: bool) -> None:
    credentials = TelegramCredentials.from_env()
    store = SessionStore.from_env()
    store.prepare()
    client = TelegramClient(
        str(store.base_path), credentials.api_id, credentials.api_hash.get_secret_value()
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await (sign_in_with_qr(client) if use_qr else sign_in(client))
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info("Logged in as user id %s", me.id)
    finally:
        await client.disconnect()
        store.secure()
    logger.info("Telegram session saved to %s", store.session_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Log in to Telegram once for the Telegram source.")
    parser.add_argument(
        "--qr", action="store_true", help="scan a QR code instead of entering a code"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(login(args.qr))


if __name__ == "__main__":
    main()
