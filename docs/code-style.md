# Code style

## Python and typing

Match the surrounding code and keep changes focused. Code must support Python
3.13, the minimum version declared in [pyproject.toml](../pyproject.toml).
That file defines Ruff's lint rules; [pyrightconfig.json](../pyrightconfig.json)
defines the source type-checking rules.

Use type annotations for public inputs and outputs. Keep public exports and
their `.pyi` declarations consistent when changing an API. Endpoint catalogue
stubs are generated; after changing the bundled inventory, run
`uv run python scripts/generate_endpoint_catalog.py` from the repository root
and commit the generated declarations. Do not edit generated stubs by hand.

## Module boundaries and public contracts

Keep reusable primitives in their existing `src/roboz` modules, reusable agent
components under `roboz.shed`, and provider catalogue code under
`roboz.endpoints`. Core modules must not import Shed, Endpoints, or provider
SDKs. Shed must not import Endpoints or provider SDKs. Endpoint client
construction remains deferred so importing the package performs no credential
lookup or network I/O.

When changing a public API, consider imports, signatures, typing, schemas,
persisted data, prompts, defaults, and side effects. Breaking changes should be
intentional and documented as described in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Docstrings

Shipped Python uses Google-style docstrings. Explain behavior, constraints,
ownership, and side effects that names and type annotations cannot convey.
Avoid repeating the signature or narrating the implementation. Add `Args`,
`Returns`, or `Raises` sections only when they provide useful information.

Ruff checks public docstrings. The repository's
[docstring test](../tests/test_docstrings.py) also requires a docstring on every
shipped module, including internal modules, and every `@tool` or `@factory`
callable. Tests, examples, and maintenance scripts are exempt from these rules.

The complete normalized docstring of a tool or factory becomes its description
in the model prompt. Write it as a concise instruction: when to use the tool,
what it does, and any important constraints or side effects. Keep Python
implementation notes in comments and avoid repeating the input schema.
Pydantic model and field descriptions exposed to the model need the same care.

Test design and mocking conventions live in
[testing practices](testing-practices.md).
