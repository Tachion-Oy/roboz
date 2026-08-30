"""Runtime context for live conversation compactification."""

from dataclasses import dataclass, field

from roboz.llm import EndpointBinding
from roboz.tooling import FactoryCtx


@dataclass
class CompactifyState:
    count: int = 0


@dataclass(frozen=True)
class CompactifyBaseCtx(FactoryCtx):
    endpoint: EndpointBinding
    system_prompt: str
    instructions: str


@dataclass(frozen=True)
class CompactifyMessagesCtx(CompactifyBaseCtx):
    threshold_percent: float
    state: CompactifyState = field(default_factory=CompactifyState)


__all__ = ["CompactifyBaseCtx", "CompactifyMessagesCtx", "CompactifyState"]
