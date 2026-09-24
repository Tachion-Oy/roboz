"""Models for the Proton Bridge tool implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

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
    """TLS protocol modes offered by the local Bridge."""

    SSL = "ssl"
    STARTTLS = "starttls"


class ProtonBridgeSettings(BaseModel):
    """Explicit connection, trust, and credential settings for Proton Bridge."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    imap_host: NonEmptyString
    imap_port: int = Field(ge=1, le=65535)
    tls_mode: ProtonBridgeTlsMode
    account_address: NonEmptyString
    username: SecretStr = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    ca_file: Path | None = None
    certificate_sha256: str | None = None
    timeout_s: float = Field(default=15.0, gt=0, allow_inf_nan=False)

    @field_validator("username", "password")
    @classmethod
    def reject_ciphertext(cls, value: SecretStr) -> SecretStr:
        """Require applications to decrypt credentials before constructing a service."""
        if value.get_secret_value().startswith("roboz:"):
            raise ValueError("decrypt the credential before configuring Proton Bridge")
        return value

    @field_validator("certificate_sha256")
    @classmethod
    def normalize_fingerprint(cls, value: str | None) -> str | None:
        """Accept colon-delimited or plain SHA-256 fingerprints."""
        return _normalize_fingerprint(value)


def _normalize_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(":", "").lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("must be a SHA-256 fingerprint")
    return normalized
