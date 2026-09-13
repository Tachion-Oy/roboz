"""Callable protocols used by tool and factory decorators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from roboz.models import Empty, Invoke, Message, Stop

if TYPE_CHECKING:
    from roboz.tooling.core import Factory, Tool


class ToolFuncProtocol[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
](Protocol):
    __name__: str

    def __call__(self, *, input: TInput, messages: list[Message]) -> TOutput: ...


class FactoryToolFuncProtocol[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx,
](Protocol):
    __name__: str

    def __call__(
        self, *, input: TInput, messages: list[Message], ctx: TCtx
    ) -> TOutput: ...


class ChainedToolDecorator[TInput: Empty](Protocol):
    def __call__[TOutput: Empty | Invoke | Stop](
        self, func: ToolFuncProtocol[TInput, TOutput]
    ) -> Tool[TInput, TOutput]: ...


class ChainedToolDecoratorUnion[TInput: Empty, TOther: Empty | Stop](Protocol):
    def __call__[TOutput: Empty | Invoke | Stop](
        self,
        func: ToolFuncProtocol[TInput, TOutput] | ToolFuncProtocol[TOther, TOutput],  # type: ignore[type-var]
    ) -> Tool[TInput, TOutput] | Tool[TOther, TOutput]: ...  # type: ignore[type-var]


class FactoryDecorator(Protocol):
    """Infer input, output, and context from a factory callable without parents."""

    def __call__[TInput: Empty, TOutput: Empty | Invoke | Stop, TCtx](
        self, func: FactoryToolFuncProtocol[TInput, TOutput, TCtx]
    ) -> Factory[TInput, TOutput, TCtx]: ...


class ChainedFactoryDecorator[TInput: Empty](Protocol):
    def __call__[TOutput: Empty | Invoke | Stop, TCtx](
        self, func: FactoryToolFuncProtocol[TInput, TOutput, TCtx]
    ) -> Factory[TInput, TOutput, TCtx]: ...


class ChainedFactoryDecoratorUnion[TInput: Empty, TOther: Empty | Stop](Protocol):
    def __call__[TOutput: Empty | Invoke | Stop, TCtx](
        self,
        func: (
            FactoryToolFuncProtocol[TInput, TOutput, TCtx]
            | FactoryToolFuncProtocol[TOther, TOutput, TCtx]  # type: ignore[type-var]
        ),
    ) -> Factory[TInput, TOutput, TCtx] | Factory[TOther, TOutput, TCtx]: ...  # type: ignore[type-var]
