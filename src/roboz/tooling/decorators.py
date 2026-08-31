from __future__ import annotations

from typing import Callable, overload

from roboz.models import Empty, Invoke, Stop
from roboz.tooling.dependencies import FactoryCtx
from roboz.tooling.core import Factory, Tool
from roboz.tooling._protocols import (
    ChainedFactoryDecorator,
    ChainedFactoryDecoratorUnion,
    ChainedToolDecorator,
    ChainedToolDecoratorUnion,
    FactoryToolFuncProtocol,
    ToolFuncProtocol,
)
from roboz.tooling._typing import (
    ParentExact,
    ParentExactSeq,
    ParentExactSeqUnion,
    ParentUnion,
)


def _always_chain(x: object) -> bool:
    return True


@overload
def tool[TInput: Empty, TOutput: Empty | Invoke | Stop](
    func: ToolFuncProtocol[TInput, TOutput],
    chained_to: None = None,
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> Tool[TInput, TOutput]: ...


@overload
def tool[TInput: Empty, TOutput: Empty | Invoke | Stop](
    func: ToolFuncProtocol[TInput, TOutput],
    chained_to: ParentExact[TInput] | ParentExactSeq[TInput],
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> Tool[TInput, TOutput]: ...


@overload
def tool[TInput: Empty, TOutput: Empty | Invoke | Stop, TOther: Empty | Stop](
    func: ToolFuncProtocol[TInput, TOutput],
    chained_to: ParentUnion[TInput, TOther],
    chain_condition: Callable[[TInput | TOther], bool],
) -> Tool[TInput, TOutput]: ...


# Decorator overload for seq chaining with mixed-output parents: requires an explicit
# chain_condition so the caller acknowledges that not every parent output reaches the child.
@overload
def tool[TInput: Empty, TOther: Empty | Stop](
    func: None = None,
    *,
    chained_to: ParentExactSeqUnion[TInput, TOther],
    chain_condition: Callable[[TInput | TOther], bool],
) -> ChainedToolDecoratorUnion[TInput, TOther]: ...


# Decorator overload for seq chaining with homogeneous parents (all output TInput).
@overload
def tool[TInput: Empty](
    func: None = None,
    chained_to: ParentExactSeq[TInput] = ...,
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> ChainedToolDecorator[TInput]: ...


# Decorator overload for single-parent chaining (exact or fork) or no chaining.
@overload
def tool[TInput: Empty, TOther: Empty | Stop](
    func: None = None,
    chained_to: ParentExact[TInput] | ParentUnion[TInput, TOther] | None = None,
    chain_condition: Callable[[TInput | TOther], bool] = _always_chain,
) -> ChainedToolDecoratorUnion[TInput, TOther]: ...


def tool[  # type: ignore[reportInconsistentOverload]
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TOther: Empty | Stop,
](
    func: ToolFuncProtocol[TInput, TOutput] | None = None,
    chained_to: (
        ParentExact[TInput]
        | ParentExactSeq[TInput]
        | ParentUnion[TInput, TOther]
        | None
    ) = None,
    chain_condition: (
        Callable[[TInput], bool] | Callable[[TInput | TOther], bool]
    ) = _always_chain,
) -> Tool[TInput, TOutput] | ChainedToolDecoratorUnion[TInput, TOther]:
    def decorator[TOut: Empty | Invoke | Stop](
        func: (
            ToolFuncProtocol[TInput, TOut] | ToolFuncProtocol[TOther, TOut]  # type: ignore[type-var]
        ),
    ) -> Tool[TInput, TOut] | Tool[TOther, TOut]:  # type: ignore[type-var]
        t: Tool[TInput, TOut] = Tool(
            caller=func,  # type: ignore[arg-type]
            chained_to=chained_to,
            chain_condition=chain_condition,
            description=func.__doc__ if func.__doc__ is not None else "",
        )
        return t  # type: ignore[return-value]

    if func is None:
        return decorator
    t: Tool[TInput, TOutput] = Tool(
        caller=func,
        chained_to=chained_to,
        chain_condition=chain_condition,
        description=func.__doc__ if func.__doc__ is not None else "",
    )
    return t


@overload
def factory[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx: FactoryCtx,
](
    func: FactoryToolFuncProtocol[TInput, TOutput, TCtx],
    chained_to: None = None,
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> Factory[TInput, TOutput, TCtx]: ...


@overload
def factory[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx: FactoryCtx,
](
    func: FactoryToolFuncProtocol[TInput, TOutput, TCtx],
    chained_to: ParentExact[TInput] | ParentExactSeq[TInput],
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> Factory[TInput, TOutput, TCtx]: ...


@overload
def factory[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx: FactoryCtx,
    TOther: Empty | Stop,
](
    func: FactoryToolFuncProtocol[TInput, TOutput, TCtx],
    chained_to: ParentUnion[TInput, TOther],
    chain_condition: Callable[[TInput | TOther], bool],
) -> Factory[TInput, TOutput, TCtx]: ...


@overload
def factory[TInput: Empty, TOther: Empty | Stop](
    func: None = None,
    *,
    chained_to: ParentExactSeqUnion[TInput, TOther],
    chain_condition: Callable[[TInput | TOther], bool],
) -> ChainedFactoryDecoratorUnion[TInput, TOther]: ...


@overload
def factory[TInput: Empty](
    func: None = None,
    chained_to: ParentExactSeq[TInput] = ...,
    chain_condition: Callable[[TInput], bool] = _always_chain,
) -> ChainedFactoryDecorator[TInput]: ...


@overload
def factory[TInput: Empty, TOther: Empty | Stop](
    func: None = None,
    chained_to: ParentExact[TInput] | ParentUnion[TInput, TOther] | None = None,
    chain_condition: Callable[[TInput | TOther], bool] = _always_chain,
) -> ChainedFactoryDecoratorUnion[TInput, TOther]: ...


def factory[  # type: ignore[reportInconsistentOverload]
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx: FactoryCtx,
    TOther: Empty | Stop,
](
    func: FactoryToolFuncProtocol[TInput, TOutput, TCtx] | None = None,
    chained_to: (
        ParentExact[TInput]
        | ParentExactSeqUnion[TInput, TOther]
        | ParentUnion[TInput, TOther]
        | None
    ) = None,
    chain_condition: (
        Callable[[TInput], bool] | Callable[[TInput | TOther], bool]
    ) = _always_chain,
) -> Factory[TInput, TOutput, TCtx] | ChainedFactoryDecoratorUnion[TInput, TOther]:
    def decorator[TOut: Empty | Invoke | Stop, TCtxOut: FactoryCtx](
        func: (
            FactoryToolFuncProtocol[TInput, TOut, TCtxOut]
            | FactoryToolFuncProtocol[TOther, TOut, TCtxOut]  # type: ignore[type-var]
        ),
    ) -> Factory[TInput, TOut, TCtxOut] | Factory[TOther, TOut, TCtxOut]:  # type: ignore[type-var]
        return Factory(
            func=func,  # type: ignore[arg-type]
            chained_to=chained_to,  # type: ignore[arg-type]
            chain_condition=chain_condition,
            description=func.__doc__ if func.__doc__ is not None else "",
        )

    if func is None:
        return decorator

    return Factory(
        func=func,
        chained_to=chained_to,  # type: ignore[arg-type]
        chain_condition=chain_condition,
        description=func.__doc__ if func.__doc__ is not None else "",
    )


__all__ = ["factory", "tool"]
