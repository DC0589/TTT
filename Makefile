PY ?= .venv/bin/python

.PHONY: run migrate test lint format

run:
	$(PY) manage.py runserver

migrate:
	$(PY) manage.py migrate

test:
	DEBUG=1 DATABASE_URL=sqlite:///:memory: $(PY) manage.py test --noinput --parallel

lint:
	ruff check .

format:
	ruff format .
