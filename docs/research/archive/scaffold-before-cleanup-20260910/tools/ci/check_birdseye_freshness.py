# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_PATH = ROOT / "docs/birdseye/index.json"
DEFAULT_HOT_PATH = ROOT / "docs/birdseye/hot.json"
SERIAL_PATTERN = re.compile(r"\d{5}")


class BirdseyeFreshnessError(RuntimeError):
    """Raised when Birdseye freshness validation fails to execute."""


@dataclass(frozen=True)
class BirdseyeFreshnessReport:
    failures: list[str]
    warnings: list[str]
    remediations: list[dict[str, str]]


def build_remediations(failures: list[str], *, max_verified_age_days: int | None = None) -> list[dict[str, str]]:
    """Build human-actionable remediation commands from freshness failures."""
    remediations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(reason: str, command: str) -> None:
        key = (reason, command)
        if key in seen:
            return
        seen.add(key)
        remediations.append({"reason": reason, "command": command})

    full_refresh = (
        "python -m tools.codemap.update --targets "
        "docs/birdseye/index.json,docs/birdseye/hot.json --emit index+caps"
    )
    for failure in failures:
        if "generated_at" in failure or "nodes must be" in failure or "invalid mtime" in failure:
            add("Regenerate Birdseye index and hot list", full_refresh)
        elif "missing caps file" in failure:
            match = re.search(r"node (?P<node>.+?) references missing caps file", failure)
            target = match.group("node") if match else "docs/birdseye/index.json"
            add(
                f"Regenerate capsule for {target}",
                f"python -m tools.codemap.update --targets {target} --emit index+caps --radius 1",
            )
        elif "last verified" in failure:
            match = re.search(r"node (?P<node>.+?) last verified", failure)
            target = match.group("node") if match else "docs/birdseye/hot.json"
            threshold = f" --max-verified-age-days {max_verified_age_days}" if max_verified_age_days else ""
            add(
                f"Refresh stale hot-list node {target}",
                f"python -m tools.codemap.update --targets {target} --emit index+caps --radius 1",
            )
            add(
                "Re-run Birdseye freshness check",
                f"python tools/ci/check_birdseye_freshness.py --check{threshold}",
            )
        elif "missing caps path" in failure:
            add("Regenerate Birdseye hot list entries", full_refresh)
        else:
            add("Rebuild Birdseye artifacts", full_refresh)

    return remediations


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BirdseyeFreshnessError(f"Birdseye JSON not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BirdseyeFreshnessError(f"Birdseye JSON is invalid: {path}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise BirdseyeFreshnessError(f"Birdseye JSON must be an object: {path}")
    return loaded


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BirdseyeFreshnessError(f"Invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def evaluate_birdseye_freshness(
    *,
    index_doc: Mapping[str, Any],
    hot_doc: Mapping[str, Any],
    repo_root: Path,
    now: datetime,
    max_verified_age_days: int | None = None,
) -> BirdseyeFreshnessReport:
    failures: list[str] = []
    warnings: list[str] = []

    index_generated = index_doc.get("generated_at")
    hot_generated = hot_doc.get("generated_at")
    if not isinstance(index_generated, str) or not SERIAL_PATTERN.fullmatch(index_generated):
        failures.append("index.json: generated_at must be a 5-digit serial number")
    if not isinstance(hot_generated, str) or not SERIAL_PATTERN.fullmatch(hot_generated):
        failures.append("hot.json: generated_at must be a 5-digit serial number")
    if (
        isinstance(index_generated, str)
        and isinstance(hot_generated, str)
        and index_generated != hot_generated
    ):
        failures.append(
            "index.json and hot.json must share the same generated_at update cycle"
        )

    nodes = index_doc.get("nodes")
    if not isinstance(nodes, Mapping):
        failures.append("index.json: nodes must be a mapping")
    else:
        for node_id, payload in nodes.items():
            if not isinstance(payload, Mapping):
                failures.append(f"index.json: node {node_id!r} must be an object")
                continue
            mtime = payload.get("mtime")
            if not isinstance(mtime, str) or not SERIAL_PATTERN.fullmatch(mtime):
                failures.append(f"index.json: node {node_id!r} has invalid mtime")

    hot_nodes = hot_doc.get("nodes")
    if not isinstance(hot_nodes, list):
        failures.append("hot.json: nodes must be an array")
    else:
        stale_cutoff = (
            now - timedelta(days=max_verified_age_days)
            if max_verified_age_days is not None
            else None
        )
        for node in hot_nodes:
            if not isinstance(node, Mapping):
                failures.append("hot.json: each node entry must be an object")
                continue
            node_id = str(node.get("id", "<unknown>"))
            caps_path_raw = node.get("caps")
            if not isinstance(caps_path_raw, str) or not caps_path_raw:
                failures.append(f"hot.json: node {node_id} is missing caps path")
            else:
                caps_path = repo_root / caps_path_raw
                if not caps_path.exists():
                    failures.append(f"hot.json: node {node_id} references missing caps file {caps_path_raw}")
            verified_raw = node.get("last_verified_at")
            if verified_raw is not None:
                if not isinstance(verified_raw, str):
                    failures.append(f"hot.json: node {node_id} has invalid last_verified_at")
                else:
                    verified_at = _parse_timestamp(verified_raw)
                    if stale_cutoff is not None and verified_at < stale_cutoff:
                        failures.append(
                            f"hot.json: node {node_id} last verified at {verified_raw} exceeds "
                            f"{max_verified_age_days} day freshness window"
                        )

    return BirdseyeFreshnessReport(
        failures=failures,
        warnings=warnings,
        remediations=build_remediations(
            failures,
            max_verified_age_days=max_verified_age_days,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Birdseye generated artifacts and freshness metadata"
    )
    parser.add_argument("--index-path", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--hot-path", type=Path, default=DEFAULT_HOT_PATH)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root used to resolve capsule paths",
    )
    parser.add_argument(
        "--max-verified-age-days",
        type=int,
        help="Fail when hot list last_verified_at is older than this many days",
    )
    parser.add_argument("--check", action="store_true", help="Validate Birdseye artifacts")
    parser.add_argument(
        "--remediation-output",
        type=Path,
        help="Write remediation command suggestions as JSON.",
    )
    args = parser.parse_args(argv)

    try:
        report = evaluate_birdseye_freshness(
            index_doc=_load_json(args.index_path),
            hot_doc=_load_json(args.hot_path),
            repo_root=args.repo_root,
            now=datetime.now(UTC),
            max_verified_age_days=args.max_verified_age_days,
        )
    except BirdseyeFreshnessError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    summary = {
        "index_path": str(args.index_path),
        "hot_path": str(args.hot_path),
        "failures": report.failures,
        "warnings": report.warnings,
        "remediations": report.remediations,
    }
    if args.remediation_output:
        args.remediation_output.parent.mkdir(parents=True, exist_ok=True)
        args.remediation_output.write_text(
            json.dumps(report.remediations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False))
    if report.warnings:
        print("Birdseye freshness warnings:", file=sys.stderr)
        for warning in report.warnings:
            print(f"- {warning}", file=sys.stderr)
    if report.failures:
        print("Birdseye freshness failures:", file=sys.stderr)
        for failure in report.failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
