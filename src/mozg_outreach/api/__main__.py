"""Run the service on localhost: `uv run python -m mozg_outreach.api`."""

import logging
import os

import uvicorn

from mozg_outreach.api.app import create_app
from mozg_outreach.api.settings import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    port = int(os.environ.get("MOZG_OUTREACH_PORT", "8742"))
    # Local only: the API has no auth and drives the user's Claude Code login.
    uvicorn.run(create_app(Settings.from_env()), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
