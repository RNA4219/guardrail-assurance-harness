#!/usr/bin/env python
# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

"""Standalone Task/Acceptance bidirectional sync checker.

Validates:
1. All done tasks have an approved acceptance record
2. All approved acceptances reference a valid task
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASKS_DIR = _REPO_ROOT / "docs" / "tasks"
_ACCEPTANCE_DIR = _REPO_ROOT / "docs" / "acceptance"
_TERMINAL_TASK_STATUSES = frozenset(
    {"done", "completed", "resolved", "approved", "deployed", "rolled_back"}
)


@dataclass
class TaskRecord:
    task_id: str
    status: str
    file_path: Path
    acceptance_exempt: bool = False


@dataclass
class AcceptanceRecord:
    acceptance_id: str
    task_id: str
    status: str
    file_path: Path


@dataclass
class SyncReport:
    tasks: list[TaskRecord] = field(default_factory=list)
    acceptances: list[AcceptanceRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_front_matter(content: str) -> dict[str, Any]:
    """Parse YAML front matter from markdown content."""
    if not content.startswith("---"):
        return {}

    # Find the closing ---
    # Handle both \n---\n and \n--- at end of string
    end_match = re.search(r"\n---\s*$|\n---\s*\n", content[3:])
    if not end_match:
        return {}

    front_matter = content[3 : end_match.start() + 3]
    result: dict[str, Any] = {}

    for line in front_matter.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        # Remove quotes
        if len(value) >= 2:
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        result[key] = value

    return result


def _scan_tasks(tasks_dir: Path) -> list[TaskRecord]:
    """Scan all task files in the tasks directory."""
    tasks: list[TaskRecord] = []
    if not tasks_dir.exists():
        return tasks

    for task_file in tasks_dir.glob("*.md"):
        content = task_file.read_text(encoding="utf-8")
        fm = _parse_front_matter(content)
        task_id = fm.get("task_id", "")
        status = fm.get("status", "unknown").lower()
        acceptance_exempt = (
            "\u4f8b\u5916\u7406\u7531" in content
            and "Acceptance record \u4e0d\u8981" in content
        )

        if task_id:
            tasks.append(TaskRecord(
                task_id=task_id,
                status=status,
                file_path=task_file,
                acceptance_exempt=acceptance_exempt,
            ))

    return tasks


def _scan_acceptances(acceptance_dir: Path) -> list[AcceptanceRecord]:
    """Scan all acceptance files in the acceptance directory."""
    acceptances: list[AcceptanceRecord] = []
    if not acceptance_dir.exists():
        return acceptances

    for acc_file in acceptance_dir.glob("AC-*.md"):
        content = acc_file.read_text(encoding="utf-8")
        fm = _parse_front_matter(content)
        acceptance_id = fm.get("acceptance_id", "")
        task_id = fm.get("task_id", "")
        status = fm.get("status", "unknown").lower()

        if acceptance_id:
            acceptances.append(AcceptanceRecord(
                acceptance_id=acceptance_id,
                task_id=task_id,
                status=status,
                file_path=acc_file,
            ))

    return acceptances


def validate_bidirectional_sync(
    tasks: list[TaskRecord],
    acceptances: list[AcceptanceRecord],
) -> SyncReport:
    """Validate bidirectional sync between tasks and acceptances."""
    report = SyncReport(tasks=tasks, acceptances=acceptances)

    # Build lookup maps
    task_by_id = {t.task_id: t for t in tasks}
    acceptance_by_task_id: dict[str, list[AcceptanceRecord]] = {}
    for acc in acceptances:
        if acc.task_id:
            acceptance_by_task_id.setdefault(acc.task_id, []).append(acc)

    # Check 1: All completed tasks have an approved acceptance unless exempt.
    for task in tasks:
        if task.status in _TERMINAL_TASK_STATUSES and not task.acceptance_exempt:
            accs = acceptance_by_task_id.get(task.task_id, [])
            approved_accs = [a for a in accs if a.status == "approved"]
            if not approved_accs:
                if not accs:
                    report.errors.append(
                        f"Completed task '{task.task_id}' has no acceptance record"
                    )
                else:
                    report.warnings.append(
                        f"Completed task '{task.task_id}' has acceptance record(s) but none approved"
                    )

    # Check 2: All approved acceptances reference a valid task
    for acc in acceptances:
        if acc.status == "approved":
            if not acc.task_id:
                report.errors.append(
                    f"Approved acceptance '{acc.acceptance_id}' has no task_id reference"
                )
                continue

            referenced_task = task_by_id.get(acc.task_id)
            if not referenced_task:
                report.errors.append(
                    f"Approved acceptance '{acc.acceptance_id}' references "
                    f"non-existent task '{acc.task_id}'"
                )
            elif referenced_task.status not in _TERMINAL_TASK_STATUSES:
                report.warnings.append(
                    f"Approved acceptance '{acc.acceptance_id}' references "
                    f"task '{acc.task_id}' with status '{referenced_task.status}' (expected 'done')"
                )

    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate bidirectional sync between Task Seeds and Acceptance Records."
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=_TASKS_DIR,
        help="Directory containing Task Seed files.",
    )
    parser.add_argument(
        "--acceptance-dir",
        type=Path,
        default=_ACCEPTANCE_DIR,
        help="Directory containing Acceptance Record files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON.",
    )

    args = parser.parse_args(argv)

    tasks = _scan_tasks(args.tasks_dir)
    acceptances = _scan_acceptances(args.acceptance_dir)

    report = validate_bidirectional_sync(tasks, acceptances)

    if args.json:
        import json
        output = {
            "tasks": [
                {"task_id": t.task_id, "status": t.status, "file": str(t.file_path)}
                for t in report.tasks
            ],
            "acceptances": [
                {"acceptance_id": a.acceptance_id, "task_id": a.task_id,
                 "status": a.status, "file": str(a.file_path)}
                for a in report.acceptances
            ],
            "errors": report.errors,
            "warnings": report.warnings,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"Tasks scanned: {len(tasks)}")
        print(f"Acceptances scanned: {len(acceptances)}")

    for warning in report.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if report.errors:
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Task/Acceptance bidirectional sync is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())