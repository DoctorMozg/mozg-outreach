"""Run the service on localhost: `uv run python -m mozg_outreach.api`."""

import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from mozg_outreach.api.app import create_app
from mozg_outreach.api.settings import Settings

ENV_FILE = ".env"
logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    env_file = Path.cwd() / ENV_FILE
    # Variables already set in the shell win over the file.
    if load_dotenv(env_file):
        logger.info("loaded environment variables from %s", env_file)
    port = int(os.environ.get("MOZG_OUTREACH_PORT", "8742"))
    # Local only: the API has no auth and drives the user's Claude Code login.
    uvicorn.run(create_app(Settings.from_env()), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
