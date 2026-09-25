# Contributing

## Setup

```bash
make setup      # uv sync --extra dev
make demo       # bring up postgres + LocalStack and run the end-to-end demo
```

Docker is required for the demo and for the default test database (Testcontainers). To run tests against an existing PostgreSQL instead, set `TEST_DATABASE_URL`.

## Before opening a pull request

```bash
make lint
make test
make tf-validate
make web-check   # npm ci, typecheck, selfcheck, production bundle, size budget
```

All four must pass. CI runs the same steps plus a Docker image build; `make setup` and CI both install with `uv sync --locked`, so a dependency change must update `uv.lock` in the same commit.

## Conventions

- Python 3.12, ruff for linting and formatting (line length 100).
- Single-line conventional commit messages: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- Schema changes go through Alembic: edit `panelist/models.py`, run `uv run alembic revision --autogenerate -m "..."`, review the generated file, and make sure `uv run alembic check` reports no drift.
- Every behaviour change needs a test in `tests/`. Tests hit the HTTP API through the FastAPI test client and assert on database state where it matters.
- Keep expert-facing responses free of hidden fields; `TaskExpertView` is the only shape experts receive.
- No secrets in the repository. API keys are stored as SHA-256 hashes; the bootstrap key is supplied at runtime.
