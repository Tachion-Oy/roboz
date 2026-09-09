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


def test_release_requires_validation_before_approved_production_upload():
    release = workflow("release.yml")
    jobs = release["jobs"]
    assert set(release["on"]) == {"push"}
    assert set(release["on"]["push"]["tags"]) == {
        "roboz-v*",
        "roboshed-v*",
        "roboz-endpoints-v*",
        "roboz-proton-bridge-v*",
    }
    assert jobs["verify"]["needs"] == "preparation"
    assert set(jobs["select"]["needs"]) == {"preparation", "verify"}
    assert jobs["publish"]["needs"] == "select"
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish"]["if"] == (
        "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"
    )
    publishers = {
        name
        for name, job in jobs.items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert publishers == {"publish"}
    steps = jobs["publish"]["steps"]
    assert not any(
        "checkout" in step.get("uses", "") or "run" in step for step in steps
    )
    assert steps[-1]["uses"].startswith("pypa/gh-action-pypi-publish@")
    assert steps[-1].get("with", {}).get("skip-existing", "false") == "false"
    assert "--published-dependencies" in str(jobs["select"]["steps"])
    assert "testpypi" not in str(release).lower()
