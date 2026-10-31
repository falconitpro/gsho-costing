.PHONY: dev test seed up
dev:            ## backend в dev-режиме (AUTH_PROVIDER=dev, SQLite не поддерживается — нужен PostgreSQL)
	cd backend && uvicorn app.main:app --reload
test:
	cd backend && python -m pytest -q
seed:
	cd backend && python -m scripts.seed
up:
	docker compose up --build -d
