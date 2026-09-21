"""Generate editor declarations from the endpoint inventory.

Run ``uv run python scripts/generate_endpoint_catalog.py`` after editing models
or providers. Commit all generated .pyi files. ``--check`` checks freshness
without writing; package imports and builds never regenerate these files.
"""

import argparse
import inspect
from pathlib import Path

from roboz.endpoints import inventory
from roboz.endpoints.adapters.openai_compatible import OpenAICompatibleAdapter
from roboz.endpoints.catalog import Catalog
from roboz.endpoints._inventory_codegen import collection_declaration, spec_type_name


HEADER = '''"""Generated types for Pylance; DO NOT EDIT.

Source: src/roboz/endpoints/inventory.py
Regenerate: uv run python scripts/generate_endpoint_catalog.py
Check: uv run python scripts/generate_endpoint_catalog.py --check
"""

'''


def render() -> dict[str, str]:
    """Describe inventory collections without selecting or materializing models."""
    catalog_lines = [
        "from collections.abc import Mapping",
        "from typing import Self",
        "from roboz.endpoints.adapters.openai_compatible import OpenAICompatibleAdapter",
        "from roboz.endpoints.specs import ModelSpec",
        "",
        "class Catalog[Spec: ModelSpec]:",
        f'    """{inspect.getdoc(Catalog)}"""',
        "    api_name: str",
        "    models: tuple[Spec, ...]",
        "    models_by_attribute: Mapping[str, Spec]",
        "    _adapter: OpenAICompatibleAdapter",
        "    _stream: bool",
    ]
    for method in (Catalog.__init__, Catalog.configured, Catalog.__dir__):
        signature = str(inspect.signature(method))
        for prefix in ("collections.abc.", "typing.", f"{OpenAICompatibleAdapter.__module__}."):
            signature = signature.replace(prefix, "")
        doc = "\n".join("        " + line if line else "" for line in (inspect.getdoc(method) or "").splitlines()).lstrip()
        if "\n" in doc:
            doc += "\n        "
        catalog_lines.extend([
            "",
            f"    def {method.__name__}{signature}:",
            f'        """{doc}"""',
            "        ...",
        ])
    inventory_lines = [
        "from roboz.llm import LLMEndpoint, TranscriptionEndpoint",
        "from roboz.endpoints.catalog import Catalog",
        "from roboz.endpoints.specs import (",
        "    ChatModelSpec as ChatModelSpec,",
        "    ModelSpec as ModelSpec,",
        "    TranscriptionModelSpec as TranscriptionModelSpec,",
        ")",
        "",
        "CATALOGS: dict[str, Catalog[ChatModelSpec] | Catalog[TranscriptionModelSpec] | Catalog[ModelSpec]]",
    ]
    for name, collection in inventory.CATALOGS.items():
        spec_type = spec_type_name(collection.models_by_attribute)
        inventory_lines.extend(["", *collection_declaration(name, collection.models_by_attribute)])
        inventory_lines.extend([
            f"{name.upper()}_MODELS: tuple[{spec_type}, ...]",
        ])
    catalog_lines.append("")
    inventory_lines.extend(["", f"__all__ = {inventory.__all__!r}", ""])
    return {
        "catalog.pyi": HEADER + "\n".join(catalog_lines),
        "inventory.pyi": HEADER + "\n".join(inventory_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Fail if generated output is missing or stale; do not write",
    )
    args = parser.parse_args()
    directory = Path(inventory.__file__).parent
    stale = False
    for filename, expected in render().items():
        target = directory / filename
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != expected:
                print(f"Missing or stale generated file: {target}")
                stale = True
            else:
                print(f"Up to date: {target}")
        else:
            target.write_text(expected, encoding="utf-8", newline="\n")
            print(f"Generated: {target}")
    if stale:
        print("Run: uv run python scripts/generate_endpoint_catalog.py")
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())
