"""Environment-variable loading for required runtime credentials."""

import os
from enum import StrEnum

from dotenv import find_dotenv, load_dotenv


def load_key(required_key: StrEnum) -> str:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    try:
        return os.environ[required_key.value]
    except KeyError:
        raise ValueError(
            f"{required_key.value} not found in environment variables. "
            "Please set it in your .env file."
        )
