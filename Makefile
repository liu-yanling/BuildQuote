# BuildQuote —— 一条命令能起
PY := python
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install migrate up test bootstrap

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -r requirements.txt

migrate:
	$(BIN)/python -m app.db.migrate up

up:
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port 8000

test:
	$(BIN)/python -m pytest -q

bootstrap: install migrate
	@echo "BuildQuote 已就绪，运行 make up 启动。"
