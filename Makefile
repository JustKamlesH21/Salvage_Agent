# Salvage — Razorpay Buildathon Track 03
#
# `make verify` is the gate. Nothing is "done" until it is green.

SHELL := /usr/bin/env bash
PY    := .venv/bin/python
PIP   := .venv/bin/pip
# Python 3.12 is the target (CLAUDE.md). Override if your interpreter is elsewhere:
#   make install PYTHON=/opt/homebrew/bin/python3.12
PYTHON ?= python3

.PHONY: help install dev api web tunnel test verify seed batch chain clean

help:
	@echo "make install   create .venv, install python + node deps"
	@echo "make dev       api + web, dry-run mode"
	@echo "make test      pytest"
	@echo "make verify    THE gate: lint, types, tests, boundaries, clauses, chain, web"
	@echo "make tunnel    expose the API to Razorpay on your reserved ngrok domain"
	@echo "make seed      create test-mode plans/subscriptions in Razorpay"
	@echo "make batch     run the A/B/C eval"
	@echo "make chain     verify the audit hash chains only"

install:
	$(PYTHON) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	cd web && npm install

dev:
	@echo "DRY_RUN is the default. Arming needs POST /control/arm with the confirm token — and live dispatch is still an unimplemented stub, so armed changes the posture and nothing else."
	@trap 'kill 0' EXIT; \
	  DRY_RUN=true $(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000 & \
	  (cd web && npm run dev) & \
	  wait

api:
	DRY_RUN=true $(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd web && npm run dev

# The webhook URL registered in Razorpay has to keep working across restarts.
# Free ngrok issues a new address every time unless you run it against a
# reserved domain, so this reads PUBLIC_WEBHOOK_URL from .env and always asks
# for the same one. Set it once, register it in Razorpay once, done.
tunnel:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if [ -z "$$PUBLIC_WEBHOOK_URL" ]; then \
	  echo "PUBLIC_WEBHOOK_URL is not set in .env."; \
	  echo ""; \
	  echo "  1. dashboard.ngrok.com -> Domains -> Create Domain"; \
	  echo "     The free plan includes exactly one. Whatever name it gives"; \
	  echo "     you is fine — you are not going to type it again."; \
	  echo ""; \
	  echo "  2. Put it in .env, with the scheme:"; \
	  echo "       PUBLIC_WEBHOOK_URL=https://<that-domain>"; \
	  echo ""; \
	  echo "  3. make tunnel"; \
	  echo ""; \
	  echo "  4. In Razorpay -> Settings -> Webhooks, set the URL to"; \
	  echo "       https://<that-domain>/webhooks/razorpay"; \
	  echo "     Edit the webhook you already have. Do not add a second one."; \
	  echo ""; \
	  echo "It must be a domain you reserved. A random ngrok address from an"; \
	  echo "old session is not one, and ngrok rejects it on the free plan."; \
	  echo ""; \
	  echo "No reserved domain? Plain \`ngrok http 8000\` still works — but the"; \
	  echo "address changes every restart and Razorpay needs re-editing each time."; \
	  exit 1; \
	fi; \
	host=$${PUBLIC_WEBHOOK_URL#https://}; host=$${host#http://}; host=$${host%%/*}; \
	echo "tunnel  : https://$$host  ->  127.0.0.1:8000"; \
	echo "register: https://$$host/webhooks/razorpay   (payment.failed only)"; \
	echo ""; \
	ngrok http 8000 --url=$$host

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
