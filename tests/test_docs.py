"""The README tables are the documentation of record, so a drift between them and the code fails.

The table checks compare sets, so a new route or setting has to be documented in the same commit,
and a row for something that no longer exists has to be deleted. The benchmark check reads every
figure the section quotes out of the session artifacts it names: each round's readings, the ranges
across sessions, and the commit, run window, load, machine, CPU count and PostgreSQL and Python
versions each session ran under, so a quoted latency belongs to a recorded run and so do the
conditions it is quoted with. The figures its prose derives from those readings, the percentages
between sessions and the ratios between servers, are computed again from the artifacts. The last
check pins the test total quoted in the Testing section to the test functions in this directory.
"""

import ast
import json
import re
from pathlib import Path
from statistics import median

from panelist.config import Settings
from panelist.main import app

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
PARAM = re.compile(r"\{[^}]+\}")
SERVERS = ("one worker, in process", "`uvicorn --workers 4`")
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
NUMBERS = ("no", "one", "two", "three", "four", "five", "six")
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
    four_workers = api == SERVERS[1]
    return [
        phase
        for run in runs
        if bool(run["run"]["base_url"]) is four_workers
        for phase in run["rows"]
        if phase["claimants"] == int(claimants)
    ]


def _pairs(runs: list[dict], claimants: int) -> list[tuple[dict, dict]]:
    """Each round's in-process phase beside the four-worker phase of the same round."""
    one, four = (_phases(runs, api, claimants) for api in SERVERS)
    return list(zip(one, four, strict=True))


def _past(later: list[dict], earlier: list[dict], api: str, claimants: int, key: str, above=True):
    """How far a later session's readings sit past an earlier one's extreme, in whole percent."""
    edge = (max if above else min)(phase[key] for phase in _phases(earlier, api, claimants))
    sign = 1 if above else -1
    gaps = [sign * 100 * (phase[key] - edge) / edge for phase in _phases(later, api, claimants)]
    return str(round(min(gaps))), str(round(max(gaps)))


def _span(values: list[float]) -> tuple[str, str]:
    return f"{min(values):.1f}", f"{max(values):.1f}"


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
    assert sorted(sessions) == ["1", "2"], "the benchmark section no longer lists both sessions"

    ran_on = RAN_ON.search(" ".join(section.split()))
    assert ran_on is not None, "the benchmark section no longer says where the sessions ran"
    machine, cpus, postgres, python = ran_on.groups()
    for run in (run for runs in sessions.values() for run in runs):
        env = run["environment"]
        quoted = (machine, int(cpus), postgres, python)
        assert (env["machine"], env["cpu_count"], env["postgres"], env["python"]) == quoted

    readings = READING_ROW.findall(section)
    rows = sorted(reading[:3] for reading in readings)
    wanted = sorted((n, c, api) for n in sessions for c in ("1", "40") for api in SERVERS)
    assert rows == wanted, "the readings need one row per session, claimant count and server"
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
    rows = sorted(bounds[:2] for bounds in ranges)
    wanted = sorted((c, api) for c in ("1", "40") for api in SERVERS)
    assert rows == wanted, "the ranges need one row per claimant count and server"
    for claimants, api, *bounds in ranges:
        measured = [phase for runs in sessions.values() for phase in _phases(runs, api, claimants)]
        keys = ("p50_ms", "p95_ms", "throughput")
        for key, low, high in zip(keys, bounds[::2], bounds[1::2], strict=True):
            values = [phase[key] for phase in measured]
            assert [min(values), max(values)] == [float(low), float(high)], f"{api} {key}"


def test_readme_benchmark_prose_follows_from_its_artifacts():
    section = _benchmark_section()
    prose = " ".join(section.split())
    sessions = _sessions(section)
    first, second = sessions["1"], sessions["2"]
    one, four = SERVERS
    contended = [pair for runs in sessions.values() for pair in _pairs(runs, 40)]
    solo = [pair for runs in sessions.values() for pair in _pairs(runs, 1)]
    ahead = sum(a["p50_ms"] < b["p50_ms"] for a, b in solo)
    derived = {
        r"its contended p50 readings sit (\d+) to (\d+) percent above session 1's highest": _past(
            second, first, one, 40, "p50_ms"
        ),
        r"highest, its single-claimant p50 readings (\d+) to (\d+) percent above,": _past(
            second, first, one, 1, "p50_ms"
        ),
        r"its contended throughput (\d+) to (\d+) percent below session 1's lowest": _past(
            second, first, one, 40, "throughput", above=False
        ),
        r"four-worker server its single-claimant p50 readings sit (\d+) to (\d+) percent": _past(
            second, first, four, 1, "p50_ms"
        ),
        r"one worker's p50 is ([\d.]+) to ([\d.]+) times four workers'": _span(
            [a["p50_ms"] / b["p50_ms"] for a, b in contended]
        ),
        r"four workers claim ([\d.]+) to ([\d.]+) times as fast": _span(
            [b["throughput"] / a["throughput"] for a, b in contended]
        ),
        r"within ([\d.]+) to ([\d.]+) ms of each other in every pair of rounds": _span(
            [abs(a["p50_ms"] - b["p50_ms"]) for a, b in solo]
        ),
        r"the in-process one ahead in (\w+) of the (\w+),": (NUMBERS[ahead], NUMBERS[len(solo)]),
    }
    for pattern, figures in derived.items():
        quoted = re.search(pattern, prose)
        assert quoted is not None, f"the benchmark prose no longer says {pattern!r}"
        assert quoted.groups() == figures, f"{pattern!r} should read {figures}"

    moved = re.search(r"each server's median moved by more than (\d+) ms", prose)
    assert moved is not None, "the benchmark prose no longer says how far the medians moved"
    for api in SERVERS:
        medians = [median(p["p50_ms"] for p in _phases(r, api, 1)) for r in (first, second)]
        assert abs(medians[1] - medians[0]) > int(moved.group(1)), api


def test_readme_test_count_matches_the_suite():
    quoted = re.search(r"`make test` runs (\d+) tests", README)
    assert quoted is not None, "the README Testing section no longer states a test count"
    assert int(quoted.group(1)) == _test_functions()
