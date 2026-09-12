#!/usr/bin/env python
# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

"""Assess workflow-cookbook adoption tiers for downstream repositories."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_ROOT = ROOT / "templates"


@dataclass(frozen=True)
class TierDefinition:
    tier: int
    name: str
    purpose: str
    required_paths: tuple[str, ...]


TIERS: tuple[TierDefinition, ...] = (
    TierDefinition(0, "Minimal", "Entry point only", ("README.md",)),
    TierDefinition(1, "Foundation", "AI navigation and scope definition", ("HUB.codex.md", "BLUEPRINT.md")),
    TierDefinition(2, "Operational", "Execution, guardrails, and acceptance baseline", ("RUNBOOK.md", "GUARDRAILS.md", "EVALUATION.md")),
    TierDefinition(
        3,
        "Full",
        "Task, acceptance, and Birdseye traceability",
        (
            "docs/acceptance",
            "docs/tasks",
            "docs/birdseye/index.json",
            "docs/birdseye/hot.json",
            "docs/birdseye/caps",
        ),
    ),
)

TEMPLATE_TARGETS: dict[str, str] = {
    "HUB.codex.md.template": "HUB.codex.md",
    "BLUEPRINT.md.template": "BLUEPRINT.md",
    "RUNBOOK.md.template": "RUNBOOK.md",
    "GUARDRAILS.md.template": "GUARDRAILS.md",
    "EVALUATION.md.template": "EVALUATION.md",
}


def _parse_front_matter(content: str) -> dict[str, str]:
    if not content.startswith("---"):
        return {}
    match = re.search(r"\n---\s*(?:\n|$)", content[3:])
    if not match:
        return {}
    payload = content[3 : match.start() + 3]
    values: dict[str, str] = {}
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        rendered = value.strip()
        if len(rendered) >= 2 and rendered[0] in {"'", '"'} and rendered[-1] == rendered[0]:
            rendered = rendered[1:-1]
        values[key.strip()] = rendered
    return values


def _path_status(repo: Path, rel_path: str) -> dict[str, Any]:
    target = repo / rel_path
    exists = target.exists()
    return {
        "path": rel_path,
        "exists": exists,
        "kind": "dir" if target.is_dir() else "file",
    }


def _cumulative_required_paths(tier: int) -> list[str]:
    paths: list[str] = []
    for definition in TIERS:
        if definition.tier > tier:
            break
        paths.extend(definition.required_paths)
    return paths


def _highest_complete_tier(repo: Path) -> int:
    current_tier = -1
    for definition in TIERS:
        required = _cumulative_required_paths(definition.tier)
        if all((repo / rel_path).exists() for rel_path in required):
            current_tier = definition.tier
    return current_tier


def _template_drift(repo: Path, template_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not template_root.exists():
        return checks
    for template_name, target_name in TEMPLATE_TARGETS.items():
        template_path = template_root / template_name
        target_path = repo / target_name
        if not template_path.exists() or not target_path.exists():
            continue
        template_fm = _parse_front_matter(template_path.read_text(encoding="utf-8"))
        target_fm = _parse_front_matter(target_path.read_text(encoding="utf-8"))
        template_version = template_fm.get("template_version")
        target_version = target_fm.get("template_version")
        drifted = bool(template_version and target_version and template_version != target_version)
        checks.append(
            {
                "path": target_name,
                "template": template_name,
                "template_version": template_version,
                "document_template_version": target_version,
                "drifted": drifted,
            }
        )
    return checks


def assess_repo(repo: Path, *, check_drift: bool = False, template_root: Path = DEFAULT_TEMPLATE_ROOT) -> dict[str, Any]:
    target = repo.expanduser().resolve()
    current_tier = _highest_complete_tier(target)
    tier_name = TIERS[current_tier].name if current_tier >= 0 else "Unclassified"
    purpose = TIERS[current_tier].purpose if current_tier >= 0 else "README.md is missing"
    next_tier = current_tier + 1 if current_tier + 1 < len(TIERS) else None
    required_by_tier: dict[str, list[dict[str, Any]]] = {}
    for definition in TIERS:
        required_by_tier[str(definition.tier)] = [_path_status(target, rel_path) for rel_path in definition.required_paths]

    missing_for_next: list[str] = []
    if next_tier is not None:
        missing_for_next = [
            rel_path
            for rel_path in _cumulative_required_paths(next_tier)
            if not (target / rel_path).exists()
        ]

    drift_checks = _template_drift(target, template_root) if check_drift else []
    return {
        "repo": str(target),
        "current_tier": current_tier,
        "current_tier_name": tier_name,
        "purpose": purpose,
        "next_tier": next_tier,
        "missing_for_next_tier": missing_for_next,
        "required_by_tier": required_by_tier,
        "drift_checks": drift_checks,
        "drifted": any(check["drifted"] for check in drift_checks),
    }


def _load_repo_list(path: Path) -> list[Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("repo-list JSON must be an array")
    repos: list[Path] = []
    for index, item in enumerate(payload):
        if isinstance(item, str):
            repos.append(Path(item))
        elif isinstance(item, Mapping) and isinstance(item.get("repo"), str):
            repos.append(Path(str(item["repo"])))
        else:
            raise ValueError(f"repo-list item {index} must be a string or an object with repo")
    return repos


def _render_text(result: dict[str, Any]) -> str:
    lines = [
        f"Repo: {result['repo']}",
        f"Current Tier: {result['current_tier']} ({result['current_tier_name']})",
        "",
    ]
    for tier, statuses in result["required_by_tier"].items():
        rendered = ", ".join(
            f"{'OK' if item['exists'] else 'MISSING'} {item['path']}" for item in statuses
        )
        lines.append(f"Tier {tier}: {rendered}")
    if result["missing_for_next_tier"]:
        lines.extend(["", "Recommendation:"])
        for path in result["missing_for_next_tier"]:
            lines.append(f"- Add {path}")
    if result["drift_checks"]:
        lines.extend(["", "Template drift:"])
        for check in result["drift_checks"]:
            state = "DRIFT" if check["drifted"] else "OK"
            lines.append(
                f"- {state} {check['path']} "
                f"(doc={check['document_template_version'] or 'n/a'}, template={check['template_version'] or 'n/a'})"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess workflow-cookbook adoption tier.")
    parser.add_argument("--repo", type=Path, help="Repository to assess.")
    parser.add_argument("--repo-list", type=Path, help="JSON array of repositories to assess.")
    parser.add_argument("--template-root", type=Path, default=DEFAULT_TEMPLATE_ROOT)
    parser.add_argument("--min-tier", type=int, choices=[0, 1, 2, 3], help="Minimum tier required when --check is set.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when min tier or drift checks fail.")
    parser.add_argument("--check-drift", action="store_true", help="Compare document template_version with templates.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    args = parser.parse_args(argv)

    if args.repo is None and args.repo_list is None:
        parser.error("provide --repo or --repo-list")

    try:
        repos = _load_repo_list(args.repo_list) if args.repo_list else [args.repo]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results = [
        assess_repo(repo, check_drift=args.check_drift, template_root=args.template_root)
        for repo in repos
        if repo is not None
    ]

    if args.json:
        payload: Any = results[0] if len(results) == 1 else results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(_render_text(result) for result in results))

    failed = False
    if args.check:
        if args.min_tier is not None:
            failed = any(int(result["current_tier"]) < args.min_tier for result in results)
        if args.check_drift:
            failed = failed or any(bool(result["drifted"]) for result in results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
