from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel

from roboz.models._schema import (
    extract_fields_and_type_names,
    get_constituent_types,
    schema_scrubber,
)
from roboz.models._serialization import camel_to_snake, reduce_escapes
from roboz.runtime import Output, bind_output, get_bound_output, load_key, reset_output
from roboz.runtime._paths import delete_agent_context, get_file_count


class ExampleUnionModel(BaseModel):
    value: int | str


class HiddenPayload(BaseModel):
    secret: str


class PublicPayload(BaseModel):
    title: str
    hidden: HiddenPayload


class HiddenFieldModel(BaseModel):
    hidden: HiddenPayload


class EnvKey(StrEnum):
    sample = "ROBOZ_TEST_SAMPLE_KEY"


def test_load_key_loads_dotenv_from_current_working_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(EnvKey.sample.value, raising=False)
    (tmp_path / ".env").write_text(
        f"{EnvKey.sample.value}=from-cwd\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert load_key(EnvKey.sample) == "from-cwd"


def test_get_constituent_types_handles_union_and_single_type() -> None:
    annotation = ExampleUnionModel.model_fields["value"].annotation
    assert annotation is not None
    assert get_constituent_types(annotation) == (int, str)
    assert get_constituent_types(int) == (int,)


def test_scrub_schema_for_agent_removes_internal_fields_and_defs() -> None:

    cleaned = schema_scrubber(PublicPayload.model_json_schema(), HiddenFieldModel)

    assert "hidden" in PublicPayload.model_json_schema()["properties"]
    assert "$defs" in PublicPayload.model_json_schema()
    assert "hidden" not in cleaned["properties"]
    assert "$defs" not in cleaned
    assert cleaned["required"] == ["title"]


def test_extract_fields_and_type_names_uses_model_types() -> None:
    fields, type_names = extract_fields_and_type_names(HiddenFieldModel)

    assert fields == frozenset({"hidden"})
    assert "HiddenPayload" in type_names
    assert "hiddenpayload" in type_names


def test_camel_to_snake_converts_common_cases() -> None:
    assert camel_to_snake("CamelCase") == "camel_case"
    assert camel_to_snake("HTTPRequest") == "h_t_t_p_request"


def test_reduce_escapes_collapses_multiple_backslashes() -> None:
    text = '\\\\\\"quoted\\\\\\\\nline'
    assert reduce_escapes(text) == '\\"quoted\\nline'


def test_get_file_count_counts_only_files_and_handles_missing_path(
    tmp_path: Path,
) -> None:
    assert get_file_count(None) is None
    assert get_file_count(tmp_path / "missing") is None

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "subdir").mkdir()

    assert get_file_count(tmp_path) == 2


@pytest.mark.parametrize("agent", ["../outside", "nested/agent", "/tmp/outside"])
def test_delete_agent_context_rejects_non_child_paths(
    tmp_path: Path, agent: str
) -> None:
    root = tmp_path / "agents"
    root.mkdir()

    with pytest.raises(ValueError, match="direct child"):
        delete_agent_context(root=root, agent=agent)


def test_delete_agent_context_deletes_only_named_direct_child(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    target = root / "worker"
    sibling = root / "other"
    target.mkdir(parents=True)
    sibling.mkdir()

    delete_agent_context(root=root, agent="worker")

    assert not target.exists()
    assert sibling.is_dir()


def test_delete_agent_context_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "worker").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="resolve to a direct child"):
        delete_agent_context(root=root, agent="worker")

    assert outside.is_dir()


def test_bind_output_sets_current_context() -> None:
    assert get_bound_output() is None
    token = bind_output(Output.API)
    try:
        assert get_bound_output() == Output.API
    finally:
        reset_output(token)
    assert get_bound_output() is None
