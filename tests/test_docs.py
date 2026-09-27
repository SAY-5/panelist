"""The README tables are the documentation of record, so a drift between them and the code fails.

The table checks compare sets, so a new route or setting has to be documented in the same commit,
and a row for something that no longer exists has to be deleted. The benchmark check reads every
figure the section quotes out of the session artifacts it names: each round's readings, the ranges
across sessions, and the commit, run window, load, machine, CPU count and PostgreSQL and Python
versions each session ran under, so a quoted latency belongs to a recorded run and so do the
conditions it is quoted with. The last check pins the test total quoted in the Testing section to
the test functions in this directory.
"""

import ast
import json
import re
from pathlib import Path

from panelist.config import Settings
from panelist.main import app

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
PARAM = re.compile(r"\{[^}]+\}")
API = r"(one worker, in process|`uvicorn --workers 4`)"
BOUNDS = r"([\d.]+) to ([\d.]+)"
READINGS = r"([\d.,\s]+)"
SESSION_ROW = re.compile(
    r"^\| (\d+) \| `(docs/bench-[\w-]+\.json)` \| ([0-9a-f]{7}) \| (\d{4}-\d{2}-\d{2})"
    rf" (\d{{2}}:\d{{2}}:\d{{2}}) to (\d{{2}}:\d{{2}}:\d{{2}}) \| {BOUNDS} \| {BOUNDS} \|$",
    re.M,
)
READING_ROW = re.compile(
    rf"^\| (\d+) \| (\d+) \| {API} \| (\d+) \| {READINGS} \| {READINGS} \| {READINGS} \| (\d+) \|$",
    re.M,
)
RANGE_ROW = re.compile(rf"^\| (\d+) \| {API} \| {BOUNDS} \| {BOUNDS} \| {BOUNDS} \|$", re.M)
RAN_ON = re.compile(
    r"Both sessions ran on (.+?) with (\d+) CPUs, PostgreSQL ([\d.]+) in the compose container"
    r" and Python ([\d.]+)\."
)
# Served by FastAPI itself, not part of the platform's surface.
BUILT_IN = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _documented_routes() -> set[tuple[str, str]]:
    rows = re.findall(r"^\| (GET|POST|PUT|PATCH|DELETE) \| `([^`]+)` \|", README, re.M)
    return {(method, PARAM.sub("{}", path)) for method, path in rows}


def _actual_routes() -> set[tuple[str, str]]:
    paths = app.openapi()["paths"]
    return {
        (method.upper(), PARAM.sub("{}", path))
        for path, operations in paths.items()
        if path not in BUILT_IN
        for method in operations
    }


def _readings(cell: str) -> list[float]:
    """One table cell's comma-separated readings, one per round."""
    return [float(value) for value in cell.split(",")]


def _benchmark_section() -> str:
    start = README.index("### Claim-path benchmark")
    return README[start : README.index("\n### ", start)]


def _phases(runs: list[dict], api: str, claimants: str) -> list[dict]:
    """The phases one server ran at one claimant count, in the order the artifact holds them."""
    over_socket = api.startswith("`")
    return [
        phase
        for run in runs
        if bool(run["run"]["base_url"]) is over_socket
        for phase in run["rows"]
        if phase["claimants"] == int(claimants)
    ]


def _sessions(section: str) -> dict[str, list[dict]]:
    """Each session's runs, after checking the commit, run window and load its row quotes."""
    sessions = {}
    for number, path, commit, day, first, last, *load in SESSION_ROW.findall(section):
        runs = json.loads((ROOT / path).read_text())["runs"]
        env = [run["environment"] for run in runs]
        assert {e["commit"][:7] for e in env} == {commit}, f"{path} did not run at {commit}"
        started = sorted(run["run"]["started_at"] for run in runs)
        assert [started[0], started[-1]] == [f"{day}T{first}Z", f"{day}T{last}Z"]
        starts = [e["load_average_start"][0] for e in env]
        ends = [e["load_average_end"][0] for e in env]
        assert [min(starts), max(starts), min(ends), max(ends)] == [float(v) for v in load]
        sessions[number] = runs
    return sessions


def _test_functions() -> int:
    """Top-level `test_` functions in this directory, which is what pytest collects here."""
    return sum(
        1
        for path in Path(__file__).resolve().parent.glob("test_*.py")
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )


def test_readme_api_table_matches_the_routes():
    actual, documented = _actual_routes(), _documented_routes()
    assert actual - documented == set(), "routes missing from the README API table"
    assert documented - actual == set(), "README API table lists routes that no longer exist"


def test_readme_configuration_table_matches_the_settings():
    documented = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", README, re.M))
    actual = {name.upper() for name in Settings.model_fields}
    assert actual - documented == set(), "settings missing from the README configuration table"
    assert documented - actual == set(), "README configuration table lists settings that are gone"


def test_readme_benchmark_table_quotes_its_artifact():
    section = _benchmark_section()
    sessions = _sessions(section)
    assert len(sessions) == 2, "the benchmark section no longer lists both sessions"

    ran_on = RAN_ON.search(" ".join(section.split()))
    assert ran_on is not None, "the benchmark section no longer says where the sessions ran"
    machine, cpus, postgres, python = ran_on.groups()
    for run in (run for runs in sessions.values() for run in runs):
        env = run["environment"]
        quoted = (machine, int(cpus), postgres, python)
        assert (env["machine"], env["cpu_count"], env["postgres"], env["python"]) == quoted

    readings = READING_ROW.findall(section)
    assert len(readings) == 4 * len(sessions), "a session lost a row per server and claimant count"
    for session, claimants, api, claims, p50, p95, throughput, doubled in readings:
        measured = _phases(sessions[session], api, claimants)
        assert measured, f"session {session} has no run on {api} at {claimants} claimants"
        assert [phase["claims"] for phase in measured] == [int(claims)] * len(measured)
        assert [phase["p50_ms"] for phase in measured] == _readings(p50)
        assert [phase["p95_ms"] for phase in measured] == _readings(p95)
        assert [phase["throughput"] for phase in measured] == _readings(throughput)
        handed_twice = [phase["claims"] - phase["distinct"] for phase in measured]
        assert handed_twice == [int(doubled)] * len(measured)

    ranges = RANGE_ROW.findall(section)
    assert len(ranges) == 4, "the range table no longer has a row per server and claimant count"
    for claimants, api, *bounds in ranges:
        measured = [phase for runs in sessions.values() for phase in _phases(runs, api, claimants)]
        keys = ("p50_ms", "p95_ms", "throughput")
        for key, low, high in zip(keys, bounds[::2], bounds[1::2], strict=True):
            values = [phase[key] for phase in measured]
            assert [min(values), max(values)] == [float(low), float(high)], f"{api} {key}"


def test_readme_test_count_matches_the_suite():
    quoted = re.search(r"`make test` runs (\d+) tests", README)
    assert quoted is not None, "the README Testing section no longer states a test count"
    assert int(quoted.group(1)) == _test_functions()
