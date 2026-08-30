from __future__ import annotations

from typing import Any, Sequence

from roboz.models import Empty, Stop

from roboz.tooling.core import Factory, Tool

type ParentExact[TInput: Empty] = Tool[Any, TInput] | Factory[Any, TInput, Any]
type ParentUnion[TInput: Empty, TOther: Empty | Stop] = (
    Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]
)
type ParentExactSeq[TInput: Empty] = Sequence[
    Tool[Any, TInput] | Factory[Any, TInput, Any]
]
type ParentExactSeqUnion[TInput: Empty, TOther: Empty | Stop] = Sequence[
    Tool[Any, TInput | TOther] | Factory[Any, TInput | TOther, Any]
]
