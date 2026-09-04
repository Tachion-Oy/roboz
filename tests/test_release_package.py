import pytest

from scripts.release_package import resolve_tag


@pytest.mark.parametrize(
    "tag,name",
    [
        ("roboz-v0.1.1", "roboz"),
        ("roboz-shed-v0.1.0a1", "roboz-shed"),
        ("roboz-openai-v0.1.0a1", "roboz-openai"),
        ("roboz-proton-bridge-v0.1.0b1", "roboz-proton-bridge"),
    ],
)
def test_release_selects_one_matching_package(tag, name):
    assert resolve_tag(tag) == name


@pytest.mark.parametrize(
    "tag", ["v0.1.0", "other-v0.1.0", "roboz-v9.0.0", "roboz-shed-v0.1.0"]
)
def test_release_rejects_unknown_packages_and_version_mismatches(tag):
    with pytest.raises(ValueError):
        resolve_tag(tag)
