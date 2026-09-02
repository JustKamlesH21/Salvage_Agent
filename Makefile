# Salvage — Razorpay Buildathon Track 03
#
# `make verify` is the gate. Nothing is "done" until it is green.

SHELL := /usr/bin/env bash
PY    := .venv/bin/python
PIP   := .venv/bin/pip
# Python 3.12 is the target (CLAUDE.md). Override if your interpreter is elsewhere:
#   make install PYTHON=/opt/homebrew/bin/python3.12
PYTHON ?= python3

.PHONY: help install dev api web test verify seed batch chain clean

help:
	@echo "make install   create .venv, install python + node deps"
	@echo "make dev       api + web, dry-run mode"
	@echo "make test      pytest"
	@echo "make verify    THE gate: lint, types, tests, boundaries, clauses, chain, web"
	@echo "make seed      create test-mode plans/subscriptions in Razorpay"
	@echo "make batch     run the A/B/C eval"
	@echo "make chain     verify the audit hash chains only"

install:
	$(PYTHON) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	cd web && npm install

dev:
	@echo "DRY_RUN is the default. Going live requires api.executor.arm.arm(token) — no live HTTP route yet."
	@trap 'kill 0' EXIT; \
	  DRY_RUN=true $(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000 & \
	  (cd web && npm run dev) & \
	  wait

api:
	DRY_RUN=true $(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd web && npm run dev

test:
	.venv/bin/pytest --cov --cov-report=term-missing

verify:
	@bash scripts/verify.sh

chain:
	$(PY) scripts/verify_chain.py

seed:
	$(PY) scripts/seed_razorpay.py

batch:
	$(PY) -m eval.harness

clean:
	rm -rf .venv web/node_modules web/dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
