# Docstring Guide

Roboz uses Google-style Python docstrings for its shipped source. Docstrings
describe contracts: what a module owns, what a class represents, and what a
callable guarantees. They should explain information that names and type hints
cannot, rather than narrating the implementation.

## Required Coverage

Every shipped module has a module docstring, including package `__init__.py`
files and internal modules whose names begin with an underscore.

Public classes, constructors, functions, methods, properties, and relevant
magic methods have docstrings. Private helpers need docstrings when their
contract, invariants, side effects, or failure modes are not evident from the
code. Tests, examples, and maintenance scripts are not required to meet this
coverage rule.

Document an overloaded callable on its implementation. Leave `@overload`
signatures without docstrings, as required by Ruff's D418 rule.

## Form

Start with a short summary sentence ending in punctuation. Use an imperative
verb for a function or method (for example, "Return the configured endpoint.")
and describe the responsibility or value represented by a module or class.

Add a blank line and more detail only when it helps a caller. Use Google-style
`Args`, `Returns`, `Yields`, `Raises`, and `Attributes` sections when they carry
semantic information such as accepted ranges, units, mutation, ownership, or
intentional exceptions. Do not repeat type annotations, defaults visible in the
signature, or the implementation line by line.

If you include an `Args:` section, document every applicable parameter, even
when only one needs a detailed explanation. Ruff's D417 checks section
completeness; implicit `self` and `cls` parameters need no entry. A concise
summary or prose paragraph without an `Args:` section is also valid.

```python
def load_snapshot(path: Path, *, limit: int) -> Snapshot:
    """Load a bounded conversation snapshot.

    Args:
        path: Persisted snapshot to read.
        limit: Maximum number of messages retained from the tail.

    Raises:
        ValueError: If the persisted payload does not describe a snapshot.
    """
```

Module docstrings state the module's responsibility and, when useful, its
boundary with neighboring modules. Class docstrings explain the represented
concept and its important invariants. Constructor docstrings focus on
initialization semantics rather than repeating the class summary.

## Tools and Factories

A callable decorated with `@tool` or `@factory` has a second audience: the
agent. Roboz cleans the docstring's indentation and surrounding whitespace and
stores the complete result in `Tool.description`. When the tool is exposed as
an active tool, that description is inserted directly into the model prompt.
It is therefore part of the model-facing API, not merely developer reference
text.

Write a tool docstring as a concise instruction to the agent. State when to use
the tool, its observable effect, important constraints or side effects, and the
meaning of its outcome. Do not include Python-only details about `input`,
`messages`, factory `ctx`, or internal chaining. The input model schema is
already shown beside the description, so do not duplicate self-evident fields
or add boilerplate `Args` and `Returns` sections.

Apply this rule to passive and chained tools too. Composition can later expose
one as an active tool, and its description also appears in developer-facing
agent inspection.

```python
@rz.tool
def summarize(input: SummaryRequest, messages: list[rz.Message]) -> rz.Str:
    """Summarize the requested material while preserving cited source names."""
```

Keep implementation notes in comments or in API docstrings that are not used
to construct model prompts. Pydantic model docstrings need particular care,
as described below.

## Model Schemas

Pydantic class docstrings can become schema descriptions. Roboz includes these
descriptions in structured-output prompts and in nested input-model schemas
shown beside tool descriptions. Field descriptions also appear in schemas.
Treat this text as part of the model-facing API whenever the model is exposed
to an agent.

Describe the meaning of the data and any constraints that help the agent supply
or interpret it. Keep implementation notes out of these model docstrings and
field descriptions; use comments or developer-only documentation instead.

## Enforcement

Ruff selects the `D` rule family, filtered by the Google convention, for shipped
source. Imperative wording remains a review convention: the Google convention
does not enable D401. Review also checks factual accuracy, useful contract
details, and suitability for model prompts.

The repository policy test in [`../tests/test_docstrings.py`](../tests/test_docstrings.py)
additionally checks all shipped modules in core, Shed, OpenAI, and Proton
Bridge, including underscore modules, and every `@tool` or `@factory` callable
because Ruff's public-symbol rules do not cover all of those cases. Run
`uv run ruff check` and `uv run pytest tests/test_docstrings.py` from the
repository root to check enforcement.
