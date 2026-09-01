#!/usr/bin/env bash
# Frontend half of step 7. Split out so verify.sh stays readable.
set -uo pipefail
cd "$(dirname "$0")/../web" || exit 2

if [ ! -d node_modules ]; then
  echo "web: FAIL — node_modules is missing. Run: make install"
  exit 1
fi

rc=0
npx --no-install tsc -b --noEmit || rc=1
npm run --silent lint || rc=1
npm run --silent build || rc=1
exit $rc
