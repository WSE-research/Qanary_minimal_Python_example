#!/usr/bin/env bash
# End-to-end test runner for the Qanary minimal Python example.
#
# Builds the Java component, brings the whole stack up with docker compose,
# waits for it to be ready, then runs the predefined question/answer cases in
# e2e/testcases.json via e2e/e2e_test.py. The answers come from the bundled
# local Wikidata subset (Wikidata-dataset/), so the run is deterministic and
# needs no public Wikidata Query Service — i.e. an offline E2E test.
#
# This is the single entry point used both by CI (.github/workflows/e2e-tests.yml)
# and locally:
#
#     ./run-e2e-tests.sh                 # build + up + test + tear down
#     KEEP_RUNNING=1 ./run-e2e-tests.sh  # leave the stack running afterwards
#
# Requirements: docker + docker compose, a JDK 21 (JAVA_HOME) and Maven (for the
# Java component), and python3. The Qanary framework is resolved from Maven
# Central, so no local framework install is needed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

KEEP_RUNNING="${KEEP_RUNNING:-0}"
COMPOSE="docker compose"

teardown() {
  if [ "$KEEP_RUNNING" = "1" ]; then
    echo "KEEP_RUNNING=1 -> leaving the stack running."
  else
    echo "Tearing down the stack..."
    $COMPOSE down -v --remove-orphans || true
  fi
}
trap teardown EXIT

echo "==> 1/5 Building the Java component (LD-Java)"
bash "$HERE/build.sh"

echo "==> 2/5 Starting the stack (docker compose up --build)"
$COMPOSE up -d --build

# The Wikidata-Dataset-Loader is a one-shot init container that PUT-replaces the
# subset into the dataset triplestore. Wait for it to finish before testing,
# otherwise a query may hit the brief window while the graph is being replaced
# (especially when a previous run left stale data in the dataset volume).
echo "==> 3/5 Waiting for the dataset loader to finish"
if loader_exit="$(docker wait Wikidata-Dataset-Loader 2>/dev/null)" && [ "$loader_exit" = "0" ]; then
  echo "Dataset loader finished successfully."
else
  echo "Dataset loader did not complete successfully (exit ${loader_exit:-unknown}); logs:"
  $COMPOSE logs wikidata-dataset-loader || true
  exit 1
fi

echo "==> 4/5 Preparing the Python test environment"
VENV="$HERE/.e2e-venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$HERE/e2e/requirements.txt"

echo "==> 5/5 Running the end-to-end test cases"
set +e
"$VENV/bin/python" "$HERE/e2e/e2e_test.py"
status=$?
set -e

if [ "$status" -ne 0 ]; then
  echo "E2E tests FAILED (exit $status). Recent container logs:"
  $COMPOSE logs --tail=40 || true
fi

exit "$status"
