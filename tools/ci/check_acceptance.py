# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REQUIRED_FIELDS: Sequence[str] = (
    "acceptance_id",
    "task_id",
    "intent_id",
    "owner",
    "status",
    "reviewed_at",
    "reviewed_by",
)

REQUIRED_HEADINGS: Sequence[str] = (
    "## Scope",
    "## Acceptance Criteria",
    "## Evidence",
    "## Verification Result",
)

EXCLUDED_FILES: Sequence[str] = (
    "README.md",
    "ACCEPTANCE_TEMPLATE.md",
    "INDEX.md",
)


def _extract_front_matter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines[:1] != ["---"]:
        return []
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return []
    return lines[1:end]


def _parse_fields(front_matter_lines: Iterable[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in front_matter_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip()
        for index, char in enumerate(value):
            if char == "#" and (index == 0 or value[index - 1].isspace()):
                value = value[:index].rstrip()
                break
        data[key.strip()] = value
    return data


def validate_acceptance_docs(root: Path) -> dict[Path, list[str]]:
    docs_root = root / "docs" / "acceptance"
    missing: dict[Path, list[str]] = {}
    if not docs_root.is_dir():
        return {docs_root: ["docs/acceptance directory is missing"]}

    for md_path in sorted(docs_root.glob("*.md")):
        if md_path.name in EXCLUDED_FILES:
            continue

        problems: list[str] = []
        fields = _parse_fields(_extract_front_matter_lines(md_path))
        absent_fields = [field for field in REQUIRED_FIELDS if not fields.get(field)]
        if absent_fields:
            problems.extend(f"front matter:{field}" for field in absent_fields)

        body = md_path.read_text(encoding="utf-8")
        for heading in REQUIRED_HEADINGS:
            if heading not in body:
                problems.append(f"heading:{heading}")

        if problems:
            missing[md_path] = problems

    return missing


def _format_missing(missing: dict[Path, list[str]]) -> str:
    return "\n".join(f"{path}: missing {', '.join(items)}" for path, items in missing.items())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate acceptance records under docs/acceptance.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when acceptance records are invalid.")
    parser.add_argument("root", nargs="?", default=Path.cwd(), type=Path, help="Repository root to scan.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    missing = validate_acceptance_docs(root)
    if not missing:
        return 0

    message = _format_missing(missing)
    if args.check:
        print(message, file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
