from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader keeps GitHub's "on" key as a string instead of YAML 1.1's boolean.
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


def test_ci_cannot_publish_and_keeps_shared_verification():
    ci = workflow("ci.yml")
    verify = workflow("verify.yml")
    assert ci["jobs"]["verify"]["uses"] == "./.github/workflows/verify.yml"
    assert verify["on"] == {"workflow_call": ""}
    for document in (ci, verify):
        assert document["permissions"] == {"contents": "read"}
        for job in document["jobs"].values():
            assert "environment" not in job
            assert not any(
                "pypi-publish" in step.get("uses", "") for step in job.get("steps", [])
            )


def test_release_requires_preparation_staging_and_production_approval_environment():
    release = workflow("release.yml")
    jobs = release["jobs"]
    assert jobs["verify"]["needs"] == "preparation"
    assert set(jobs["select"]["needs"]) == {"preparation", "verify"}
    assert jobs["publish-testpypi"]["needs"] == "select"
    assert set(jobs["check-testpypi"]["needs"]) == {"preparation", "publish-testpypi"}
    assert jobs["publish"]["needs"] == "check-testpypi"
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish-testpypi"]["environment"] == "testpypi"
    assert set(jobs["check-pypi"]["needs"]) == {"preparation", "publish"}
    publishers = {
        name
        for name, job in jobs.items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert publishers == {"publish", "publish-testpypi"}
    for name in publishers:
        assert not any(
            "checkout" in step.get("uses", "") for step in jobs[name]["steps"]
        )


def test_manual_rehearsal_has_no_production_switch():
    release = workflow("release.yml")
    inputs = release["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"package"}
    assert set(inputs["package"]["options"]) == {
        "roboz",
        "roboshed",
        "roboz-openai",
        "roboz-proton-bridge",
    }
    assert (
        release["jobs"]["publish"]["if"]
        == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"
    )
    check = release["jobs"]["check-testpypi"]["steps"][-1]
    assert (
        check["env"]["DEPENDENCY_INDEX"]
        == "${{ github.event_name == 'push' && 'pypi' || 'testpypi' }}"
    )
