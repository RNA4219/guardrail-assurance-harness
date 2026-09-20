"""CI履歴の凍結・同run配布・成功観測の集約。履歴はテスト成功の代替にしない。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import unittest
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import decode_document
from gah.wire import canonical_bytes

PROFILE_FIELDS = ("runner_profile", "python_version", "image_ref", "measurement_method_version")
METHOD = "unittest-module-v1"
MAX_BYTES = 1024 * 1024


def load(path):
    with Path(path).open("rb") as stream:
        return decode_document(stream.read(MAX_BYTES + 1))


def save(path, value):
    raw = canonical_bytes(value)
    decode_document(raw)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(raw)


def profile(environment=None):
    env = os.environ if environment is None else environment
    fields = {name: env.get(name) for name in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")}
    if any(type(value) is not str or not value or len(value) > 128 for value in fields.values()):
        raise ValueError("CI_PROFILE_UNAVAILABLE")
    image = {"kind": "ci_image", "id": "github-hosted-image",
             "digest": hashlib.sha256(canonical_bytes(fields)).hexdigest()}
    return {"runner_profile": fields["RUNNER_OS"] + ":" + fields["RUNNER_ARCH"],
            "python_version": platform.python_version(), "image_ref": image,
            "measurement_method_version": METHOD}


def _validate(value):
    from tools.test_matrix import _validate_history
    return _validate_history(value)


def empty_history(context):
    from tools.test_matrix import TIMING_ALGORITHM_VERSION
    return _validate({"schema_version": 1, "kind": "ci_timing_history",
                      "algorithm_version": TIMING_ALGORITHM_VERSION, **context, "observations": []})


def same_profile(left, right):
    return all(left.get(key) == right.get(key) for key in PROFILE_FIELDS)


def prepare(previous, output, context=None):
    context = profile() if context is None else context
    history = empty_history(context)
    status = "MISSING"
    if previous is not None and Path(previous).is_file():
        old = _validate(load(previous))
        if same_profile(old, history):
            history = old
            status = "LOADED"
        else:
            status = "PROFILE_CHANGED"
    save(output, history)
    return {"history_status": status, "history_digest": hashlib.sha256(canonical_bytes(history)).hexdigest()}


def validate_execution_plan(value):
    fields = {"schema_version", "kind", "source_sha", "history_digest", "plan", "lane_modules"}
    if (type(value) is not dict or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "ci_execution_plan"
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", str(value["source_sha"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["history_digest"]))):
        raise ValueError("CI_PLAN_INVALID")
    plan = value["plan"]
    if type(plan) is not dict or type(plan.get("matrix")) is not dict:
        raise ValueError("CI_PLAN_INVALID")
    rows = plan["matrix"].get("include")
    mapping = value["lane_modules"]
    if type(rows) is not list or not rows or len(rows) > 32 or type(mapping) is not dict:
        raise ValueError("CI_PLAN_INVALID")
    lanes = []
    all_modules = []
    for row in rows:
        if (type(row) is not dict or set(row) != {"lane", "tests"}
                or type(row["lane"]) is not str or not re.fullmatch(r"[a-z0-9-]{1,64}", row["lane"])
                or type(row["tests"]) is not int or row["tests"] < 1):
            raise ValueError("CI_PLAN_INVALID")
        lane = row["lane"]
        modules = mapping.get(lane)
        if (type(modules) is not list or not modules
                or any(type(name) is not str or not re.fullmatch(r"test_[A-Za-z0-9_]+", name) for name in modules)
                or len(modules) > row["tests"]):
            raise ValueError("CI_PLAN_INVALID")
        lanes.append(lane)
        all_modules.extend(modules)
    if (len(set(lanes)) != len(lanes) or set(mapping) != set(lanes)
            or len(set(all_modules)) != len(all_modules)
            or plan.get("total_tests") != sum(row["tests"] for row in rows)
            or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("plan_digest")))):
        raise ValueError("CI_PLAN_INVALID")
    return value


def _run_id():
    value = os.environ.get("GITHUB_RUN_ID", "") + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not re.fullmatch(r"[0-9]+-[0-9]+", value):
        raise ValueError("CI_RUN_ID_MISSING")
    return value


def collect(previous, reports, output, *, plan_path=None, expected_lock=None, expected_run_id=None):
    history = _validate(load(previous))
    if plan_path is not None:
        plan = validate_execution_plan(load(plan_path))
        if (hashlib.sha256(canonical_bytes(plan)).hexdigest() != expected_lock
                or plan["source_sha"] != _source_sha()
                or hashlib.sha256(canonical_bytes(history)).hexdigest() != plan["history_digest"]
                or type(expected_run_id) is not str):
            raise ValueError("CI_PLAN_LOCK_CHANGED")
        lanes = [item["lane"] for item in plan["plan"]["matrix"]["include"]]
        expected = {"timing-" + lane + ".json" for lane in lanes}
        names = [Path(path).name for path in reports]
        if len(names) != len(expected) or set(names) != expected:
            raise ValueError("TIMING_REPORTS_INCOMPLETE")
    documents = []
    for path in reports:
        document = _validate(load(path))
        if plan_path is not None:
            lane = Path(path).name.removeprefix("timing-").removesuffix(".json")
            rows = document["observations"]
            expected_modules = plan["lane_modules"][lane]
            expected_tests = next(item["tests"] for item in plan["plan"]["matrix"]["include"] if item["lane"] == lane)
            if (len(rows) != len(expected_modules)
                    or {row["module"] for row in rows} != set(expected_modules)
                    or sum(row["test_count"] for row in rows) != expected_tests
                    or any(not row["success"] or row["source_sha"] != plan["source_sha"]
                           or row["run_id"] != expected_run_id for row in rows)):
                raise ValueError("TIMING_REPORT_BINDING_MISMATCH")
        documents.append(document)
    seen = {}
    excluded_profiles = 0
    for document in [history, *documents]:
        if not same_profile(history, document):
            # 異なるhost imageは元artifactに残るが、同条件履歴へ混ぜない。
            excluded_profiles += 1
            continue
        for row in document["observations"]:
            key = row["module"], row["run_id"]
            if key in seen and seen[key] != row:
                raise ValueError("TIMING_OBSERVATION_CONFLICT")
            seen[key] = row
    modules = {}
    for row in seen.values():
        if row["success"]:
            modules.setdefault(row["module"], []).append(row)
    observations = []
    for module in sorted(modules):
        observations.extend(sorted(modules[module], key=lambda row: (row["observed_at"], row["run_id"]))[-3:])
    result = {**history, "observations": observations}
    _validate(result)
    save(output, result)
    return {"module_count": len(modules), "observation_count": len(observations),
            "excluded_profile_reports": excluded_profiles}


def _source_sha():
    value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
        raise ValueError("CI_SOURCE_INVALID")
    # CI実測に未commitの内容を同じcommit名で紐づけない。
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT).returncode != 0:
        raise ValueError("CI_SOURCE_DIRTY")
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True)
    if any(name.startswith(("src/", "tools/", "tests/", "config/", "schemas/", ".github/"))
           for name in untracked.splitlines()):
        raise ValueError("CI_SOURCE_DIRTY")
    expected = os.environ.get("GITHUB_SHA")
    if expected is not None and value != expected:
        raise ValueError("CI_SOURCE_CHANGED")
    return value


def create_plan(history_path, output):
    from tools import test_matrix
    history = _validate(load(history_path))
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    if loader.errors:
        raise ValueError("TEST_DISCOVERY_FAILED")
    lanes, timing = test_matrix.partition_timed(suite, timing_history=history,
        timing_history_digest=hashlib.sha256(canonical_bytes(history)).hexdigest(),
        **{name: history[name] for name in PROFILE_FIELDS})
    plan = test_matrix.describe(lanes, timing=timing)
    value = {"schema_version": 1, "kind": "ci_execution_plan", "source_sha": _source_sha(),
             "history_digest": hashlib.sha256(canonical_bytes(history)).hexdigest(), "plan": plan,
             "lane_modules": {lane: sorted({type(case).__module__.removeprefix("tests.") for case in group})
                              for lane, group in lanes.items()}}
    validate_execution_plan(value)
    save(output, value)
    result = {"matrix": plan["matrix"], "digest": plan["plan_digest"],
              "lock_digest": hashlib.sha256(canonical_bytes(value)).hexdigest()}
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            for name, item in result.items():
                stream.write(name + "=" + (json.dumps(item) if isinstance(item, dict) else item) + "\n")
    return result


def run_lane(history_path, plan_path, lane, output, expected_lock):
    from tools import test_matrix
    plan = validate_execution_plan(load(plan_path))
    if (hashlib.sha256(canonical_bytes(plan)).hexdigest() != expected_lock
            or plan["source_sha"] != _source_sha()):
        raise ValueError("CI_PLAN_LOCK_CHANGED")
    history = _validate(load(history_path))
    digest = hashlib.sha256(canonical_bytes(history)).hexdigest()
    if digest != plan["history_digest"]:
        raise ValueError("CI_HISTORY_CHANGED")
    if lane not in [item["lane"] for item in plan["plan"]["matrix"]["include"]]:
        raise ValueError("UNKNOWN_TEST_LANE")
    run_id = _run_id()
    observed_profile = profile()
    profile_path = Path(output).with_suffix(".profile.json")
    save(profile_path, observed_profile)
    args = ["--lane", lane, "--expected-plan-digest", plan["plan"]["plan_digest"],
        "--timing-history", str(history_path), "--timing-history-digest", digest,
        "--source-sha", plan["source_sha"], "--timing-run-id", run_id,
        "--timing-output", str(output), "--timing-measurement-profile", str(profile_path)]
    for name in PROFILE_FIELDS:
        value = history[name]
        args += ["--" + name.replace("_", "-"),
                 json.dumps(value, separators=(",", ":")) if isinstance(value, dict) else value]
    return test_matrix.main(args)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _api(path, token):
    # 認証headerを外部hostへのredirectに引き継がない。固定GitHub APIのみ。
    request = urllib.request.Request("https://api.github.com/" + path, headers={
        "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
        "User-Agent": "GAH-CI-history", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
        raw = response.read(MAX_BYTES + 1)
    return decode_document(raw)


def previous_run(repository, branch, token, *, api=_api):
    if (type(repository) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or type(branch) is not str or not branch or len(branch) > 255):
        raise ValueError("INVALID_REPOSITORY")
    query = urllib.parse.urlencode({"branch": branch, "status": "success", "event": "push", "per_page": 3})
    result = api(f"repos/{repository}/actions/workflows/test.yml/runs?{query}", token)
    if type(result) is not dict or type(result.get("workflow_runs")) is not list:
        raise ValueError("CI_HISTORY_API_INVALID")
    for run in result["workflow_runs"]:
        if (type(run) is not dict or type(run.get("id")) is not int or run["id"] < 1 or run.get("head_branch") != branch
                or run.get("event") != "push" or run.get("conclusion") != "success"):
            continue
        data = api(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100", token)
        if type(data) is not dict or type(data.get("artifacts")) is not list:
            raise ValueError("CI_HISTORY_API_INVALID")
        if any(type(item) is dict and item.get("name") == "gah-timing-history" and item.get("expired") is False
               for item in data["artifacts"]):
            return run["id"]
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("previous")
    p.add_argument("--repository", required=True)
    p.add_argument("--branch", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--previous")
    p.add_argument("--output", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--previous", required=True)
    p.add_argument("--reports", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--expected-lock", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--history", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("lane")
    p.add_argument("--history", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--lane", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--expected-lock", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "previous":
            try:
                run_id = previous_run(args.repository, args.branch, os.environ["GH_TOKEN"])
            except (OSError, ValueError, KeyError):
                # 初回/権限不足/一時障害は明示的に履歴なし。テストgateは省略しない。
                run_id = None
            result = {"run_id": run_id}
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write("run_id=" + (str(run_id) if run_id is not None else "") + "\n")
        elif args.command == "prepare":
            result = prepare(args.previous, args.output)
        elif args.command == "plan":
            result = create_plan(args.history, args.output)
        elif args.command == "lane":
            return run_lane(args.history, args.plan, args.lane, args.output, args.expected_lock)
        else:
            reports = sorted(path for path in Path(args.reports).glob("**/timing-*.json")
                             if not path.name.endswith(".profile.json"))
            if not reports:
                raise ValueError("TIMING_REPORTS_MISSING")
            result = collect(args.previous, reports, args.output, plan_path=args.plan,
                             expected_lock=args.expected_lock, expected_run_id=_run_id())
        print(json.dumps(result))
        return 0
    except (OSError, ValueError):
        print("CI_TIMING_HISTORY_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
