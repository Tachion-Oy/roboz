"""Load and encrypt API keys in ordinary dotenv files."""

import base64
import binascii
import os
from pathlib import Path
from threading import Lock

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from dotenv import dotenv_values


_LOCK = Lock()
_API_KEY_SUFFIX = "_API_KEY"
_ENCRYPTED_NAMESPACE = "roboz:"
_ENCRYPTED_PREFIX = f"{_ENCRYPTED_NAMESPACE}v1:"
_PASSWORD_ENV = "ROBOZ_ENV_PASSWORD"
_SALT_BYTES = 16
_KEY_BYTES = 32
_KDF_ITERATIONS = 3
_KDF_LANES = 4
_KDF_MEMORY_KIB = 65536
_DEFAULT_SOURCE = Path(".env")
_DEFAULT_ENCRYPTED = Path(".env.encrypt")


def _is_api_key(name: str | None) -> bool:
    return bool(name and name.endswith(_API_KEY_SUFFIX))


def _is_encrypted(value: str) -> bool:
    # Recognize unsupported versions too, so they fail instead of loading as keys.
    return value.startswith(_ENCRYPTED_NAMESPACE)


def _has_usable_key(value: str | None) -> bool:
    return bool(value and value.strip() and not _is_encrypted(value))


def _password(explicit: str | None) -> str:
    password = explicit if explicit is not None else os.environ.pop(_PASSWORD_ENV, None)
    if not password:
        raise ValueError("An API key password is required")
    return password


def _fernet(password: str, salt: bytes) -> Fernet:
    key = Argon2id(
        salt=salt,
        length=_KEY_BYTES,
        iterations=_KDF_ITERATIONS,
        lanes=_KDF_LANES,
        memory_cost=_KDF_MEMORY_KIB,
    ).derive(password.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _decrypt(value: str, password: str) -> str:
    try:
        if not value.startswith(_ENCRYPTED_PREFIX):
            raise ValueError
        encoded_salt, token = value[len(_ENCRYPTED_PREFIX) :].split(":", 1)
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        if len(salt) != _SALT_BYTES:
            raise ValueError
        return _fernet(password, salt).decrypt(token.encode("ascii")).decode("utf-8")
    except (ValueError, InvalidToken, UnicodeError, binascii.Error) as error:
        raise ValueError("Invalid encrypted API key or password") from error


def load_api_keys(path: str | Path | None = None, *, password: str | None = None) -> None:
    """Load missing ``*_API_KEY`` values; decrypt only when needed.

    By default, prefer ``.env.encrypt`` and fall back to plaintext ``.env``.
    An environment password is consumed only when pending ciphertext exists.
    All pending values are validated before any are added to the environment.
    """
    with _LOCK:
        dotenv_path = Path(path) if path is not None else _DEFAULT_ENCRYPTED
        if path is None and not dotenv_path.exists():
            dotenv_path = _DEFAULT_SOURCE
        pending = {
            name: value
            for name, value in os.environ.items()
            if _is_api_key(name) and _is_encrypted(value)
        }
        for name, value in dotenv_values(dotenv_path, interpolate=False).items():
            if not _is_api_key(name) or not value or not value.strip():
                continue
            if name in pending or _has_usable_key(os.environ.get(name)):
                continue
            pending[name] = value

        encrypted = {name: value for name, value in pending.items() if _is_encrypted(value)}
        secret = _password(password) if encrypted else None
        resolved = dict(pending)
        if secret is not None:
            for name, value in encrypted.items():
                resolved[name] = _decrypt(value, secret)
        if any(not value.strip() for value in resolved.values()):
            raise ValueError("An encrypted API key is empty")
        os.environ.update(resolved)


def encrypt_env(path: str | Path = ".env", *, password: str | None = None) -> Path:
    """Create an encrypted dotenv file beside the untouched plaintext source.

    The output has an ``.encrypt`` suffix and contains all parsed variables.
    Repeated calls recreate that output from the source file.
    """
    dotenv_path = Path(path)
    if dotenv_path.is_symlink() or not dotenv_path.is_file():
        raise ValueError("The dotenv path must be an existing regular file")
    values = dotenv_values(dotenv_path, interpolate=False)
    keys = {name: value for name, value in values.items() if _is_api_key(name) and value and value.strip()}
    if any(_is_encrypted(value) for value in keys.values()):
        raise ValueError("The source dotenv file must contain plaintext API keys")
    if keys:
        secret = _password(password)
        for name, value in keys.items():
            salt = os.urandom(_SALT_BYTES)
            token = _fernet(secret, salt).encrypt(value.encode("utf-8")).decode("ascii")
            values[name] = f"{_ENCRYPTED_PREFIX}{base64.urlsafe_b64encode(salt).decode('ascii')}:{token}"
    output = Path(f"{dotenv_path}.encrypt")
    lines: list[str] = []
    for name, value in values.items():
        if value is None:
            lines.append(f"{name}\n")
        else:
            quoted = value.replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{name}='{quoted}'\n")
    output.write_text("".join(lines), encoding="utf-8")
    return output
