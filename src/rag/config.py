"""Application settings, loaded once from the environment / .env file."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    documents_dir: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        documents_dir=os.getenv("DOCUMENTS_DIR", "documents"),
    )
