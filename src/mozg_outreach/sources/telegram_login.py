"""One-time interactive login: python -m mozg_outreach.sources.telegram_login"""

import asyncio
import logging

from mozg_outreach.sources.telegram import SessionStore, TelegramCredentials

logger = logging.getLogger(__name__)


async def login() -> None:
    from telethon import TelegramClient

    credentials = TelegramCredentials.from_env()
    store = SessionStore.from_env()
    store.prepare()
    client = TelegramClient(
        str(store.base_path), credentials.api_id, credentials.api_hash.get_secret_value()
    )
    try:
        # Telethon prompts for the phone number, login code and 2FA password.
        await client.start()
    finally:
        await client.disconnect()
        store.secure()
    logger.info("Telegram session saved to %s", store.session_file)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(login())


if __name__ == "__main__":
    main()
