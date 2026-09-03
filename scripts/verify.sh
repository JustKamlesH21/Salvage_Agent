#!/usr/bin/env bash
# THE gate. CLAUDE.md verification order, all seven steps.
#
# Every step runs even after an earlier one fails, and the summary at the end
# lists all of them. A gate that stops at the first failure tells you one thing
# per run; this one tells you everything that is wrong in a single pass, which
# is what makes it usable for self-correction.
#
# Exit 0 only when every step passes.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m')
RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m'); RESET=$(printf '\033[0m')
if [ ! -t 1 ]; then BOLD=""; DIM=""; RED=""; GREEN=""; RESET=""; fi

# Prefer the project venv without requiring it to be activated.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
  export PATH="$PWD/.venv/bin:$PATH"
else
  PY="$(command -v python3 || true)"
fi

STEP_NAMES=(); STEP_STATUS=(); STEP_NOTES=()

step() {
  local name="$1"; local note="$2"; shift 2
  printf '\n%s──▶ %s%s  %s%s%s\n' "$BOLD" "$name" "$RESET" "$DIM" "$note" "$RESET"
  if "$@"; then
    STEP_NAMES+=("$name"); STEP_STATUS+=("pass"); STEP_NOTES+=("$note")
  else
    local code=$?
    printf '%s    step failed (exit %s)%s\n' "$RED" "$code" "$RESET"
    STEP_NAMES+=("$name"); STEP_STATUS+=("FAIL"); STEP_NOTES+=("$note")
  fi
}

# A missing tool is a failure, not a silent skip — an unrun check proves nothing.
require() {
  local name="$1"; shift
  if ! command -v "$1" >/dev/null 2>&1 && [ ! -x "$1" ]; then
    printf '%s    %s is not installed. Run: make install%s\n' "$RED" "$1" "$RESET"
    return 127
  fi
  "$@"
}

printf '%sSalvage verification%s  %s(%s)%s\n' "$BOLD" "$RESET" "$DIM" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$RESET"

step "1a lint"        "ruff check"                    require ruff ruff check .
step "1b format"      "ruff format --check"           require ruff ruff format --check .
step "2  types"       "mypy --strict api/"            require mypy mypy --strict api/
step "3  tests"       "pytest + 100% branch cov on gates/ and normalise/" \
                      require pytest pytest --cov --cov-report=term-missing
step "4  boundaries"  "Razorpay SDK confined; events/audit append-only" \
                      "$PY" scripts/check_boundaries.py
step "5  clauses"     "every gate cites; every rule explains" \
                      "$PY" scripts/check_clauses.py
step "6  chain"       "event and audit hash chains intact" \
                      "$PY" scripts/verify_chain.py
step "7  web"         "tsc --noEmit, lint, vitest, vite build" \
                      bash scripts/verify_web.sh

printf '\n%s────────────────────────────────────────────────────────────%s\n' "$DIM" "$RESET"
printf '%sSUMMARY%s\n' "$BOLD" "$RESET"
failed=0
for i in "${!STEP_NAMES[@]}"; do
  if [ "${STEP_STATUS[$i]}" = "pass" ]; then
    printf '  %s✓%s  %-14s %s%s%s\n' "$GREEN" "$RESET" "${STEP_NAMES[$i]}" "$DIM" "${STEP_NOTES[$i]}" "$RESET"
  else
    printf '  %s✗%s  %-14s %s%s%s\n' "$RED" "$RESET" "${STEP_NAMES[$i]}" "$DIM" "${STEP_NOTES[$i]}" "$RESET"
    failed=$((failed + 1))
  fi
done

if [ "$failed" -gt 0 ]; then
  printf '\n%sverify: FAIL — %s of %s step(s)%s\n' "$RED" "$failed" "${#STEP_NAMES[@]}" "$RESET"
  printf '%sFix the code. Do not weaken a check to make it pass.%s\n' "$DIM" "$RESET"
  exit 1
fi
printf '\n%sverify: OK — %s/%s%s\n' "$GREEN" "${#STEP_NAMES[@]}" "${#STEP_NAMES[@]}" "$RESET"
exit 0
