"""Keep operational documentation aligned with the repository's examples."""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / ".github" / "workflows" / "verify.yml",
)
EXAMPLE_REFERENCE = re.compile(r"examples/[A-Za-z0-9_./-]+\.py")
DOCUMENTS_WITH_EXAMPLE_REFERENCES = tuple(
    document
    for document in DOCUMENTS
    if EXAMPLE_REFERENCE.search(document.read_text(encoding="utf-8"))
)


@pytest.mark.parametrize(
    "document",
    DOCUMENTS_WITH_EXAMPLE_REFERENCES,
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_operational_example_references_exist(document: Path) -> None:
    """Reject runnable commands and links that name removed example files."""
    references = set(EXAMPLE_REFERENCE.findall(document.read_text(encoding="utf-8")))

    missing = sorted(
        reference for reference in references if not (ROOT / reference).is_file()
    )
    assert not missing, (
        f"Missing example references in {document.relative_to(ROOT)}: "
        + ", ".join(missing)
    )


def test_release_gate_and_ci_run_the_current_smoke_example() -> None:
    """Keep maintainer instructions and CI on the same existing smoke script."""
    command = "uv run python examples/simple.py"
    assert "[CONTRIBUTING.md](CONTRIBUTING.md)" in (
        ROOT / "AGENTS.md"
    ).read_text(encoding="utf-8")
    assert command in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert command in (
        ROOT / ".github" / "workflows" / "verify.yml"
    ).read_text(encoding="utf-8")
