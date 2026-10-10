"""Encrypt selected environment values and restore their original names."""

import base64
import binascii
import os
import re
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path
from threading import Lock
from typing import Final

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from dotenv import dotenv_values

ENCRYPTED_SUFFIX: Final[str] = "_ENCRYPTED"
ENCRYPTED_NAMESPACE: Final[str] = "roboz:"
DEFAULT_ENCRYPTED_ENV_PATH: Final[Path] = Path(".env.encrypt")

__all__ = [
    "DEFAULT_ENCRYPTED_ENV_PATH", "ENCRYPTED_NAMESPACE", "ENCRYPTED_SUFFIX",
    "encrypt_env", "encrypt_env_values", "decrypt_env_values", "serialize_env",
    "load_secrets",
]

_LOCK = Lock()
_ENCRYPTED_PREFIX = f"{ENCRYPTED_NAMESPACE}v1:"
_PASSWORD_ENV = "ROBOZ_ENV_PASSWORD"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SALT_BYTES = 16
_DEFAULT_SOURCE = Path(".env")


def _is_encrypted(value: str) -> bool:
    return value.startswith(ENCRYPTED_NAMESPACE)


def _has_usable_key(value: str | None) -> bool:
    return bool(value and value.strip() and not _is_encrypted(value))


def _password(explicit: str | None) -> str:
    password = explicit if explicit is not None else os.environ.pop(_PASSWORD_ENV, None)
    return _require_password(password)


def _require_password(password: str | None) -> str:
    if not password:
        raise ValueError("A secret password is required")
    return password


def _validate_name(name: str, *, stored: bool = False) -> str:
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid environment variable name")
    base = name
    if name.upper().endswith(ENCRYPTED_SUFFIX):
        if not stored or not name.endswith(ENCRYPTED_SUFFIX):
            raise ValueError("Enter a base name without the reserved _ENCRYPTED suffix")
        base = name[:-len(ENCRYPTED_SUFFIX)]
        if not _NAME.fullmatch(base) or base.upper().endswith(ENCRYPTED_SUFFIX):
            raise ValueError("Invalid encrypted variable name")
    return base


def _fernet(password: str, salt: bytes) -> Fernet:
    key = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536).derive(
        password.encode("utf-8")
    )
    return Fernet(base64.urlsafe_b64encode(key))


def _decrypt(value: str, password: str) -> str:
    try:
        if not value.startswith(_ENCRYPTED_PREFIX):
            raise ValueError
        encoded_salt, token = value[len(_ENCRYPTED_PREFIX):].split(":", 1)
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        if len(salt) != _SALT_BYTES:
            raise ValueError
        return _fernet(password, salt).decrypt(token.encode("ascii")).decode("utf-8")
    except (ValueError, InvalidToken, UnicodeError, binascii.Error) as error:
        raise ValueError("Invalid encrypted secret or password") from error


def encrypt_env_values(
    values: Mapping[str, str | None], *, secret_names: Collection[str],
    password: str | None = None,
) -> dict[str, str | None]:
    """Encrypt selected base names in memory, without reading files or environment.

    Encrypted entries acquire ``_ENCRYPTED``; ordinary values retain their names.
    The caller explicitly selects secrets, independently of their spelling.
    """
    selected = set(secret_names)
    if selected - values.keys():
        raise ValueError("A selected secret is missing from the supplied values")
    for name, value in values.items():
        _validate_name(name)
        if value is not None and ("\x00" in value or _is_encrypted(value)):
            raise ValueError("Supply plaintext environment values without NUL characters")
        if name in selected and value is None:
            raise ValueError("A selected secret must have a value")
    secret = _require_password(password) if selected else ""
    result: dict[str, str | None] = {}
    for name, value in values.items():
        if name in selected and value is not None:
            salt = os.urandom(_SALT_BYTES)
            token = _fernet(secret, salt).encrypt(value.encode("utf-8")).decode("ascii")
            result[name + ENCRYPTED_SUFFIX] = (
                f"{_ENCRYPTED_PREFIX}{base64.urlsafe_b64encode(salt).decode('ascii')}:{token}"
            )
        else:
            result[name] = value
    return result


def decrypt_env_values(
    values: Mapping[str, str | None], *, password: str | None = None,
) -> dict[str, str | None]:
    """Validate stored assignments and decrypt them into their original base names.

    No environment is mutated, and no partially decrypted result is returned.
    Plaintext assignments are included unchanged. Legacy ciphertext names fail.
    """
    result: dict[str, str | None] = {}
    for name, value in values.items():
        base = _validate_name(name, stored=True)
        if base in result:
            raise ValueError("Plaintext and encrypted assignments collide")
        if name.endswith(ENCRYPTED_SUFFIX):
            if value is None:
                raise ValueError("Invalid encrypted secret or password")
            result[base] = _decrypt(value, _require_password(password))
        elif value is not None and _is_encrypted(value):
            raise ValueError("Ciphertext names must use _ENCRYPTED; recreate legacy secrets")
        else:
            result[base] = value
    return result


def serialize_env(values: Mapping[str, str | None]) -> str:
    """Serialize literal dotenv assignments without interpolation or shell execution."""
    lines: list[str] = []
    for name, value in values.items():
        _validate_name(name, stored=True)
        if value is None:
            lines.append(f"{name}\n")
        else:
            if "\x00" in value:
                raise ValueError("Environment values cannot contain NUL characters")
            quoted = value.replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{name}='{quoted}'\n")
    return "".join(lines)


def load_secrets(path: str | Path | None = None, *, password: str | None = None) -> None:
    """Load dotenv values, decrypting ``*_ENCRYPTED`` into missing base names.

    Existing process values win, including empty overrides. With the default
    path, a manual root ``.env`` overrides ``.env.encrypt``. Validate everything
    before mutating the environment; consume an environment password only when
    decryption is needed. Retain ciphertext provenance for diagnostic redaction.
    """
    with _LOCK:
        dotenv_path = Path(path) if path is not None else DEFAULT_ENCRYPTED_ENV_PATH
        values = dict(dotenv_values(dotenv_path, interpolate=False))
        values.update({k: v for k, v in os.environ.items() if k.endswith(ENCRYPTED_SUFFIX)})
        if path is None and _DEFAULT_SOURCE.is_file():
            for name, value in dotenv_values(_DEFAULT_SOURCE, interpolate=False).items():
                base = _validate_name(name, stored=True)
                values.pop(base, None)
                values.pop(base + ENCRYPTED_SUFFIX, None)
                values[name] = value
        overrides = dict(os.environ)
        pending: dict[str, str | None] = {}
        for name, value in values.items():
            base = _validate_name(name, stored=True)
            if base not in overrides:
                pending[name] = value
        encrypted = {k: v for k, v in pending.items() if k.endswith(ENCRYPTED_SUFFIX)}
        resolved = decrypt_env_values(pending, password=_password(password) if encrypted else None)
        os.environ.update({k: v for k, v in resolved.items() if v is not None})
        os.environ.update({k: v for k, v in encrypted.items() if v is not None})


def encrypt_env(
    path: str | Path = ".env", *, secret_names: Collection[str], password: str | None = None,
) -> Path:
    """Atomically write an encrypted dotenv beside the untouched plaintext source.

    Select base names explicitly. The output has an ``.encrypt`` suffix and
    owner-only permissions; encryption never changes the source file.
    """
    dotenv_path = Path(path)
    if dotenv_path.is_symlink() or not dotenv_path.is_file():
        raise ValueError("The dotenv path must be an existing regular file")
    values = encrypt_env_values(
        dotenv_values(dotenv_path, interpolate=False), secret_names=secret_names,
        password=_password(password) if secret_names else None,
    )
    output = Path(f"{dotenv_path}.encrypt")
    if output.is_symlink():
        raise ValueError("The output must not be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialize_env(values))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output
