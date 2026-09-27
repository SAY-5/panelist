"""The README tables are the documentation of record, so a drift between them and the code fails.

The table checks compare sets, so a new route or setting has to be documented in the same commit,
and a row for something that no longer exists has to be deleted. The last check pins the test
total quoted in the Testing section to the test functions in this directory.
"""

import ast
import re
from pathlib import Path

from panelist.config import Settings
from panelist.main import app

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()
PARAM = re.compile(r"\{[^}]+\}")
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


def test_readme_test_count_matches_the_suite():
    quoted = re.search(r"`make test` runs (\d+) tests", README)
    assert quoted is not None, "the README Testing section no longer states a test count"
    assert int(quoted.group(1)) == _test_functions()
