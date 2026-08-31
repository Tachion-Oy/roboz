"""Validation for identifiers exposed to language models."""

from __future__ import annotations

import re

_PUBLIC_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", flags=re.ASCII)


def validate_public_name(name: str, *, kind: str) -> str:
    """Return ``name`` when it is a lowercase ASCII snake-case identifier."""
    if not isinstance(name, str):
        raise TypeError(f"{kind} name must be a string")
    if _PUBLIC_NAME.fullmatch(name) is None:
        raise ValueError(
            f"{kind} name must be lowercase ASCII snake_case and match "
            "'[a-z][a-z0-9]*(?:_[a-z0-9]+)*'"
        )
    return name
