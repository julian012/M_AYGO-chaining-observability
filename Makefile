.DEFAULT_GOAL := help
PY ?= python3
N ?= 300
C ?= 15

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup: ## Genera .env (secretos aleatorios) y certificado TLS
	@scripts/gen-env.sh
	@scripts/gen-certs.sh

up: setup ## Construye y levanta toda la pila
	docker compose up -d --build

down: ## Detiene la pila (conserva los logs)
	docker compose down

clean: ## Detiene la pila y BORRA volúmenes de logs
	docker compose down -v

load: ## Genera carga: make load N=500 C=20
	$(PY) scripts/generate_load.py --n $(N) --concurrency $(C) --correlator http://127.0.0.1:9100

trace: ## Consulta una traza: make trace ID=CLI-xxxxxxxxxxxx
	@set -a; . ./.env; set +a; curl -s -H "Authorization: Bearer $$CORRELATOR_TOKEN" http://127.0.0.1:9100/trace/$(ID) | $(PY) -m json.tool

stats: ## Estadísticas del correlacionador
	@set -a; . ./.env; set +a; curl -s -H "Authorization: Bearer $$CORRELATOR_TOKEN" http://127.0.0.1:9100/stats | $(PY) -m json.tool

test: ## Pruebas unitarias
	$(PY) -m pytest

lint: ## Lint + formato (ruff) y validación de la plantilla IaC (cfn-lint)
	ruff check src tests scripts && ruff format --check src tests scripts
	cfn-lint infra/template.yaml

audit: ## Auditoría de dependencias (pip-audit)
	pip-audit

.PHONY: help setup up down clean load trace stats test lint audit
