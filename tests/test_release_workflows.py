import os
from pathlib import Path
import subprocess

import pytest
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
    assert set(verify["on"]) == {"workflow_call"}
    assert "with" not in ci["jobs"]["verify"]
    source = verify["on"]["workflow_call"]["inputs"]["source-sha"]
    assert source["required"] == "false"
    assert source["default"] == ""
    for document in (ci, verify):
        assert document["permissions"] == {"contents": "read"}
        for job in document["jobs"].values():
            assert "environment" not in job
            assert not any(
                "pypi-publish" in step.get("uses", "") for step in job.get("steps", [])
            )


def test_release_requires_manual_validation_before_production_upload():
    release = workflow("release.yml")
    jobs = release["jobs"]
    assert set(release["on"]) == {"workflow_dispatch"}
    inputs = release["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"tag"}
    assert inputs["tag"]["type"] == "string"
    assert inputs["tag"]["required"] == "true"
    assert release["concurrency"] == {
        "group": "release-${{ inputs.tag }}",
        "cancel-in-progress": "false",
    }
    assert jobs["verify"]["needs"] == "preparation"
    assert set(jobs["select"]["needs"]) == {"preparation", "verify"}
    assert jobs["publish"]["needs"] == "select"
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish"]["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'"
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


def test_every_release_source_checkout_uses_the_resolved_commit():
    release = workflow("release.yml")["jobs"]
    verify = workflow("verify.yml")
    steps = release["preparation"]["steps"]
    prepare = next(step for step in steps if step.get("id") == "prepare")
    assert prepare["env"]["RELEASE_TAG"] == "${{ inputs.tag }}"
    assert '--resolve --base-commit "$GITHUB_SHA"' in prepare["run"]
    assert (
        release["preparation"]["outputs"]["sha"] == "${{ steps.prepare.outputs.sha }}"
    )
    checkout = next(
        step for step in steps if "actions/checkout@" in step.get("uses", "")
    )
    assert checkout["with"]["fetch-depth"] == "0"
    assert (
        "ref" not in checkout["with"]
    )  # workflow and resolver come from invocation SHA
    assert release["verify"]["uses"] == "./.github/workflows/verify.yml"
    assert (
        release["verify"]["with"]["source-sha"]
        == "${{ needs.preparation.outputs.sha }}"
    )
    for job in verify["jobs"].values():
        checkouts = [
            step for step in job["steps"] if "actions/checkout@" in step.get("uses", "")
        ]
        assert len(checkouts) == 1
        assert checkouts[0]["with"]["ref"] == "${{ inputs.source-sha || github.sha }}"
    selected_checkout = release["select"]["steps"][0]
    assert "actions/checkout@" in selected_checkout["uses"]
    assert selected_checkout["with"]["ref"] == "${{ needs.preparation.outputs.sha }}"
    selection = next(
        step
        for step in release["select"]["steps"]
        if "--from-dist" in step.get("run", "")
    )
    assert selection["env"]["RELEASE_TAG"] == "${{ inputs.tag }}"


@pytest.mark.parametrize(
    ("event", "ref", "allowed"),
    [
        ("workflow_dispatch", "refs/heads/main", True),
        ("workflow_dispatch", "refs/heads/feature", False),
        ("workflow_dispatch", "refs/tags/roboz-v0.1.0", False),
        ("push", "refs/heads/main", False),
        ("push", "refs/tags/roboz-v0.1.0", False),
        ("pull_request", "refs/pull/1/merge", False),
    ],
)
def test_release_entrypoint_rejects_other_events_and_refs(event, ref, allowed):
    guard = workflow("release.yml")["jobs"]["preparation"]["steps"][0]
    result = subprocess.run(
        ["bash", "-e", "-c", guard["run"]],
        env={**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref},
        capture_output=True,
    )
    assert (result.returncode == 0) is allowed
