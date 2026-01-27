.PHONY: up down build logs migrate shell psql redis test clean

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

migrate:
	docker compose exec api alembic upgrade head

migrate-down:
	docker compose exec api alembic downgrade -1

shell:
	docker compose exec api bash

psql:
	docker compose exec postgres psql -U fxlab -d fxlab

redis:
	docker compose exec redis redis-cli

restart-api:
	docker compose restart api

restart-worker:
	docker compose restart worker

clean:
	docker compose down -v
	rm -rf data/*.csv

status:
	docker compose ps

health:
	curl -s http://localhost:18000/health | python3 -m json.tool
