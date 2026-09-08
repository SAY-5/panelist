.PHONY: setup lint test migrate demo demo-down tf-validate tf-plan run

COMPOSE := docker compose -f deploy/docker-compose.yml
DEMO_DB := postgresql+psycopg://panelist:panelist@localhost:5439/panelist

setup:
	uv sync --extra dev

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q

migrate:
	uv run alembic upgrade head

run:
	uv run uvicorn panelist.main:app --host 0.0.0.0 --port 8000

demo:
	$(COMPOSE) up -d --wait postgres localstack
	DATABASE_URL=$(DEMO_DB) uv run alembic downgrade base
	DATABASE_URL=$(DEMO_DB) uv run alembic upgrade head
	DATABASE_URL=$(DEMO_DB) uv run python -m sim.demo

demo-down:
	$(COMPOSE) down -v

tf-validate:
	cd deploy/terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate

tf-plan:
	cd deploy/terraform && terraform init -input=false && terraform plan -input=false -var-file=environments/dev.tfvars
