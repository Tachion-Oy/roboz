"""Enforce separate statement coverage floors without rounding up."""

import argparse
import json
from pathlib import Path

FLOORS = {
    "src/roboz/": 95,
    "packages/shed/src/roboz_shed/": 90,
    "packages/openai/src/roboz_openai/": 90,
    "packages/proton-bridge/src/roboz_proton_bridge/": 89,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    files = json.loads(args.report.read_text())["files"]
    failed = False
    for prefix, floor in FLOORS.items():
        summaries = [
            data["summary"]
            for path, data in files.items()
            if path.replace("\\", "/").startswith(prefix)
        ]
        statements = sum(summary["num_statements"] for summary in summaries)
        covered = sum(summary["covered_lines"] for summary in summaries)
        percent = 100 * covered / statements if statements else 0
        print(f"{prefix}: {percent:.2f}% (required {floor}%)")
        failed |= not statements or covered * 100 < floor * statements
    if failed:
        raise SystemExit("Statement coverage floor failed")


if __name__ == "__main__":
    main()
