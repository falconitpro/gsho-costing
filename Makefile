# Приёмка одной командой (D-158). Требуется стенд разработки: PostgreSQL 16, backend (APP_ENV=dev, AUTH_PROVIDER=dev) и frontend запущены.
# Адреса и браузер — из окружения (значения по умолчанию — локальный стенд):
#   DATABASE_URL  — база для backend-тестов (отдельная, НЕ рабочая; тесты её наполняют)
#   BASE_URL / API_URL / BROWSER_EXECUTABLE — интерфейс, API и Chrome/Chromium для браузерных проверок
# Итог: ненулевой код выхода при любом FAIL / BLOCKED / FLAKY; отчёты — в artifacts/.

BASE_URL ?= http://localhost:5173
API_URL ?= http://localhost:8000
BROWSER_EXECUTABLE ?= $(shell command -v google-chrome || command -v chromium || command -v chromium-browser)
PY ?= python3
export BASE_URL API_URL BROWSER_EXECUTABLE

.PHONY: dev test seed up
dev:            ## backend в dev-режиме (AUTH_PROVIDER=dev, SQLite не поддерживается — нужен PostgreSQL)
	cd backend && uvicorn app.main:app --reload
test:
	cd backend && python -m pytest -q
seed:
	cd backend && python -m scripts.seed
up:
	docker compose up --build -d

.PHONY: acceptance test-backend test-frontend test-ui test-acceptance

acceptance: test-backend test-frontend test-ui test-acceptance
	@echo "Приёмка пройдена: backend, сборка, интерфейс по ролям, сценарии, 34 случая приёмки, ширины. Отчёт: artifacts/acceptance/summary.html"

test-backend:
	@test -n "$(DATABASE_URL)" || { echo "DATABASE_URL не задан — нужна отдельная тестовая база PostgreSQL 16" >&2; exit 2; }
	@mkdir -p artifacts
	cd backend && $(PY) -m pytest -q --junitxml=../artifacts/backend-junit.xml

test-frontend:
	cd frontend && npm ci && npm run build
	$(PY) scripts/ci/style_budget.py

test-ui:
	$(PY) scripts/e2e/ui_smoke.py $(BASE_URL) $(API_URL) "$(BROWSER_EXECUTABLE)"
	$(PY) scripts/e2e/scenarios.py $(BASE_URL) $(API_URL) "$(BROWSER_EXECUTABLE)"

test-acceptance:
	$(PY) scripts/acceptance/acceptance.py artifacts/acceptance
