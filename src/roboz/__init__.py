"""Lazy domain namespaces and concise authoring primitives for Roboz."""

from importlib import import_module
from typing import Final

_DOMAINS: Final = frozenset(
    {
        "agent",
        "dependencies",
        "deployment",
        "exceptions",
        "llm",
        "models",
        "runtime",
        "skill",
        "tooling",
        "tools",
    }
)
_AUTHORING_EXPORTS: Final = {
    "Agent": ("roboz.agent", "Agent"),
    "Factory": ("roboz.tooling", "Factory"),
    "Skill": ("roboz.skill", "Skill"),
    "Tool": ("roboz.tooling", "Tool"),
    "factory": ("roboz.tooling", "factory"),
    "tool": ("roboz.tooling", "tool"),
}

__all__ = list(_AUTHORING_EXPORTS) + sorted(_DOMAINS)  # pyright: ignore[reportUnsupportedDunderAll]


def __getattr__(name: str) -> object:
    """Load a declared public namespace or authoring primitive on first access."""
    if name in _DOMAINS:
        value = import_module(f"{__name__}.{name}")
    else:
        try:
            module_name, attribute = _AUTHORING_EXPORTS[name]
        except KeyError:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return loaded globals together with every declared lazy export."""
    return sorted(set(globals()) | set(__all__))
