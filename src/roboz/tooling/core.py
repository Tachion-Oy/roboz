from __future__ import annotations

import uuid
from copy import deepcopy
from logging import getLogger
from typing import Any, Callable, Sequence, Type, get_type_hints

from roboz._naming import validate_public_name
from roboz.models import Empty, Invoke, Message, Stop
from roboz.models._schema import get_constituent_types
from roboz.tooling.dependencies import (
    ExternalDependency,
    ExternalDependencySource,
    FactoryCtx,
    ToolDependency,
    dedupe_external_dependencies,
    factory_context_dependencies,
)
from roboz.tooling._protocols import FactoryToolFuncProtocol, ToolFuncProtocol

logger = getLogger(__name__)


class Tool[TInput: Empty, TOutput: Empty | Invoke | Stop]:
    def __init__[TOther: Empty | Stop](
        self,
        *,
        caller: ToolFuncProtocol[TInput, Any],
        chained_to: (
            Sequence[Tool[Any, TInput] | Factory[Any, TInput, Any]]
            | Sequence[Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]]
            | Tool[Any, TInput]
            | Factory[Any, TInput, Any]
            | Tool[Any, TInput | TOther]
            | Factory[Any, TInput | TOther, Any]
            | None
        ),
        chain_condition: (Callable[[TInput], bool] | Callable[[TInput | TOther], bool]),
        description: str = "",
        _id: str | None = None,
        _dependencies: tuple[ToolDependency[Any], ...] = (),
        _dependency_sources: tuple[ExternalDependencySource, ...] = (),
    ):
        self.caller: ToolFuncProtocol[TInput, Any] = caller
        self.name = caller.__name__
        self.InputModel, self.OutputModel = self._get_tool_signature(caller)
        self.description: str = description
        self.chained_to = None
        self.chain_condition = lambda x: True
        self.chain(chained_to=chained_to, chain_condition=chain_condition)
        self._id = _id if _id is not None else str(uuid.uuid4())
        self._dependencies = _dependencies
        self._dependency_sources = _dependency_sources

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = validate_public_name(value, kind="tool")

    def rename(self, name: str) -> Tool:
        self.name = name
        return self

    def chain[TOther: Empty | Stop](
        self,
        chained_to: (
            Sequence[Tool[Any, TInput] | Factory[Any, TInput, Any]]
            | Sequence[Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]]
            | Tool[Any, TInput]
            | Factory[Any, TInput, Any]
            | Tool[Any, TInput | TOther]
            | Factory[Any, TInput | TOther, Any]
            | None
        ),
        chain_condition: (
            Callable[[TInput], bool] | Callable[[TInput | TOther], bool] | None
        ) = None,
    ) -> Tool[TInput, TOutput]:
        if chained_to is None:
            self.chained_to = None
        else:
            new_targets = (
                [chained_to]
                if not isinstance(chained_to, Sequence)
                else list(chained_to)
            )
            existing_targets = [] if self.chained_to is None else list(self.chained_to)
            existing_targets.extend(new_targets)
            self.chained_to = existing_targets

        if chain_condition is not None:
            self.chain_condition = chain_condition
        return self

    @property
    def id(self) -> str:
        return self._id

    @property
    def dependencies(self) -> tuple[ToolDependency[Any], ...]:
        return self._dependencies

    @property
    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        candidates = [binding.resource for binding in self._dependencies]
        for source in self._dependency_sources:
            candidates.extend(source.external_dependencies())
        return dedupe_external_dependencies(candidates)

    def copy[TOther: Empty | Stop](
        self,
        *,
        name: str | None = None,
        chained_to: (
            Sequence[Tool[Any, TInput] | Factory[Any, TInput, Any]]
            | Sequence[Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]]
            | Tool[Any, TInput]
            | Factory[Any, TInput, Any]
            | Tool[Any, TInput | TOther]
            | Factory[Any, TInput | TOther, Any]
            | None
        ) = None,
        chain_condition: (
            Callable[[TInput], bool] | Callable[[TInput | TOther], bool] | None
        ) = None,
        description: str = "",
    ) -> Tool[TInput, TOutput]:
        t: Tool[TInput, TOutput] = Tool(
            caller=self.caller,
            chained_to=chained_to if chained_to else self.chained_to,  # type: ignore[arg-type]
            chain_condition=self.chain_condition
            if chain_condition is None
            else chain_condition,
            description=description if description else self.description,
            _dependencies=self._dependencies,
            _dependency_sources=self._dependency_sources,
        )
        if name is not None:
            t.name = name
            return t
        t.name = self.name
        return t

    @classmethod
    def to_tool_list(
        cls,
        items: Sequence[Tool | Sequence[Tool]] | None,
    ) -> list[Tool]:
        """Flatten ``Tool | Sequence[Tool]`` (or ``None``) into a list."""
        if items is None:
            return []
        result: list[Tool] = []
        for item in items:
            if isinstance(item, Sequence):
                result.extend(list(item))
            else:
                result.append(item)
        return result

    def _get_tool_signature(
        self,
        func: ToolFuncProtocol[TInput, Any],
    ) -> tuple[type[TInput], type[Empty | Invoke | Stop]]:
        hints = get_type_hints(func)
        if set(hints.keys()) - {"input", "messages", "return"}:
            raise ValueError(
                f"All Tools must have only 'input' and 'messages' as their arguments: {hints.keys()}"
            )

        inputModel: Type[TInput] = hints["input"]
        outputModel: type[Empty | Invoke | Stop] = hints["return"]

        if not issubclass(inputModel, Empty):
            raise ValueError(f"input must be subclass of '{Empty}'")

        output_types = get_constituent_types(outputModel)
        for arg in output_types:
            if not (
                issubclass(arg, Empty)
                or issubclass(arg, Invoke)
                or issubclass(arg, Stop)
            ):
                raise ValueError(
                    f"output must be subclass of '{Empty}', '{Invoke}' or '{Stop}', got '{arg.__name__}'"
                )
        return inputModel, outputModel

    def __call__(self, input: Invoke | Empty, messages: list[Message]) -> TOutput:
        # Chaining is an in-memory operation. Serializing nested models here
        # erases concrete payload types behind generic/base-model fields.
        # Preserve the existing field projection/exclusions, but validate a
        # detached copy of the Python values instead of their wire encoding.
        reduced_input = self.InputModel(
            **deepcopy(
                {
                    k: getattr(input, k)
                    for k in input.model_dump()
                    if k in set(self.InputModel.model_fields)
                }
            )
        )
        return self.caller(input=reduced_input, messages=messages)  # type: ignore[return-value]


class Factory[
    TInput: Empty,
    TOutput: Empty | Invoke | Stop,
    TCtx: FactoryCtx,
]:
    def __init__[TOther: Empty | Stop](
        self,
        func: FactoryToolFuncProtocol[TInput, TOutput, TCtx],
        *,
        chained_to: (
            Sequence[Tool[Any, TInput] | Factory[Any, TInput, Any]]
            | Sequence[Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]]
            | Tool[Any, TInput]
            | Factory[Any, TInput, Any]
            | Tool[Any, TInput | TOther]
            | Factory[Any, TInput | TOther, Any]
            | None
        ) = None,
        chain_condition: (Callable[[TInput], bool] | Callable[[TInput | TOther], bool]),
        description: str = "",
    ) -> None:
        self._func = func
        self._chained_to = chained_to
        self._chain_condition = chain_condition
        self.name = func.__name__
        self.description: str = description
        self._id = str(uuid.uuid4())

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = validate_public_name(value, kind="factory")

    @property
    def id(self) -> str:
        return self._id

    def __call__(self, ctx: TCtx) -> Tool[TInput, TOutput]:
        if not isinstance(ctx, FactoryCtx):
            raise TypeError("factory context must inherit FactoryCtx")

        def _func_ctx(input: TInput, messages: list[Message]) -> TOutput:
            return self._func(input=input, messages=messages, ctx=ctx)

        _func_ctx.__name__ = self._func.__name__
        _func_ctx.__annotations__ = {
            k: v for k, v in self._func.__annotations__.items() if k != "ctx"
        }
        dependencies, dependency_sources = factory_context_dependencies(ctx)
        t: Tool[TInput, TOutput] = Tool(
            caller=_func_ctx,
            chained_to=self._chained_to,
            chain_condition=self._chain_condition,
            description=self.description,
            _id=self.id,
            _dependencies=dependencies,
            _dependency_sources=dependency_sources,
        )
        return t
