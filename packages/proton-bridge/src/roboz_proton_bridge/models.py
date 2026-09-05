"""Models for the Proton Bridge tool implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from roboz_shed.tools.email.contracts import EmailProviderError

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True)
class ReplySourceHeaders:
    """Sanitized source data needed to construct one RFC-compliant reply."""

    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    message_id: str
    references: tuple[str, ...]
    sender: str
    sent_at: str | None
    quoted_body: str | None = None
    quote_warnings: tuple[str, ...] = ()


class ProtonBridgeTlsMode(StrEnum):
    """Supported TLS negotiation modes for the local IMAP bridge."""

    SSL = "ssl"
    STARTTLS = "starttls"


class ProtonBridgeSettings(BaseSettings):
    """Complete Proton Bridge runtime settings loaded from the environment."""

    model_config = SettingsConfigDict(env_prefix="ROBOZ_PROTON_BRIDGE_", extra="ignore")

    imap_host: NonEmptyString
    imap_port: int = Field(ge=1, le=65535)
    tls_mode: ProtonBridgeTlsMode
    account_address: NonEmptyString
    ca_file: Path | None = None
    certificate_sha256: str | None = None
    username: NonEmptyString
    password: SecretStr = Field(min_length=1)

    @field_validator("certificate_sha256")
    @classmethod
    def normalize_fingerprint(cls, value: str | None) -> str | None:
        """Normalize and validate an optional SHA-256 certificate fingerprint."""
        return _normalize_fingerprint(value)

    @classmethod
    def from_environment(cls) -> Self:
        """Load settings from the environment and raise a safe configuration error."""
        try:
            # BaseSettings supplies required fields from its configured sources.
            return cls()  # pyright: ignore[reportCallIssue]
        except ValidationError as error:
            missing = any(item["type"] == "missing" for item in error.errors())
            message = (
                "Proton Mail Bridge environment is not configured"
                if missing
                else "Proton Mail Bridge environment configuration is invalid"
            )
            raise EmailProviderError(message) from error


def _normalize_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(":", "").lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("must be a SHA-256 fingerprint")
    return normalized
