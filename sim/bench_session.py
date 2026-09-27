"""One benchmark session: alternating rounds on both servers, each against a fresh schema.

A round resets the schema with `alembic downgrade base` and `alembic upgrade head`, runs
`sim.bench` with its in-process server, resets again, starts a `uvicorn --workers 4` server,
runs `sim.bench --base-url` against it and stops it. Every run of either server therefore starts
from a new process with an empty connection pool, and a pair of runs shares a load window. The
four-worker server gets the settings in `sim.bench.SERVER_SETTINGS`, the ones the in-process
server runs under, and uvicorn's warning log level, so the two servers differ in their worker
count and in the socket between client and server. All runs land in one artifact, and a session
refuses a path that already holds runs, so an artifact is one session.

    DATABASE_URL=postgresql+psycopg://panelist:panelist@localhost:5439/panelist \
        uv run python -m sim.bench_session --json docs/bench-$(date -u +%F).json

The restart is for comparable rounds, not a workaround: a server kept for the whole session serves
every round too, because the engine does not prepare statements on the database side.
"""

import argparse
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sim.bench import SERVER_SETTINGS, wait_for


def run(*args: str) -> None:
    print("$ python", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], check=True)


def reset_schema() -> None:
    run("-m", "alembic", "downgrade", "base")
    run("-m", "alembic", "upgrade", "head")


@contextmanager
def multi_worker_server(port: int, workers: int, log: Path) -> Iterator[str]:
    """A `uvicorn --workers` server for one run; the environment already holds the settings."""
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "panelist.main:app",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--log-level",
        "warning",
    ]
    print("$ python", " ".join(command[1:]), flush=True)
    with log.open("a") as out:
        server = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT)
        try:
            base = f"http://127.0.0.1:{port}"
            wait_for(base)
            yield base
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a benchmark session on both servers")
    parser.add_argument("--json", metavar="PATH", required=True, help="the session's artifact")
    parser.add_argument("--rounds", type=int, default=3, help="rounds on each server")
    parser.add_argument("--port", type=int, default=8767, help="port of the multi-worker server")
    parser.add_argument("--workers", type=int, default=4, help="its worker processes")
    parser.add_argument("--server-log", default="dist/bench-server.log", help="its output")
    args = parser.parse_args(argv)

    if Path(args.json).exists():
        raise SystemExit(f"{args.json} already holds runs; a session starts a new artifact")
    log = Path(args.server_log)
    log.parent.mkdir(parents=True, exist_ok=True)
    print("server settings:", ", ".join(f"{k}={os.environ[k]!r}" for k in SERVER_SETTINGS))
    for n in range(1, args.rounds + 1):
        print(f"round {n} of {args.rounds}", flush=True)
        reset_schema()
        run("-m", "sim.bench", "--json", args.json)
        reset_schema()
        with multi_worker_server(args.port, args.workers, log) as base:
            run("-m", "sim.bench", "--base-url", base, "--json", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
