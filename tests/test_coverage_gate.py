import json
import sys

import pytest

from scripts import check_coverage


def test_separate_coverage_floor_cannot_hide_behind_total(tmp_path, monkeypatch):
    report = tmp_path / "coverage.json"
    files = {
        prefix + "module.py": {
            "summary": {"num_statements": 1000, "covered_lines": 1000}
        }
        for prefix in check_coverage.FLOORS
    }
    files["packages/openai/src/roboz_openai/module.py"]["summary"]["covered_lines"] = (
        899
    )
    report.write_text(json.dumps({"files": files}))
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", str(report)])
    with pytest.raises(SystemExit, match="floor failed"):
        check_coverage.main()
    files["packages/openai/src/roboz_openai/module.py"]["summary"]["covered_lines"] = (
        900
    )
    report.write_text(json.dumps({"files": files}))
    check_coverage.main()


def test_missing_distribution_coverage_fails(tmp_path, monkeypatch):
    report = tmp_path / "coverage.json"
    report.write_text('{"files": {}}')
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", str(report)])
    with pytest.raises(SystemExit, match="floor failed"):
        check_coverage.main()
