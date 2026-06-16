#!/bin/sh
# Load the Wikidata subset into the local dataset Virtuoso, on demand, once the
# Virtuoso instance has started. PUT replaces the graph content, so re-running
# (e.g. on `docker compose up` again) is idempotent.
set -eu

BASE="http://localhost:8891"
GRAPH="urn:wikidata"
TTL="/data/wikidata-subset.ttl"

echo "[loader] waiting for the dataset Virtuoso SPARQL endpoint at $BASE/sparql ..."
i=0
until curl -fsS "$BASE/sparql?query=ASK%20%7B%7D" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 90 ]; then
        echo "[loader] timed out waiting for the dataset Virtuoso" >&2
        exit 1
    fi
    sleep 2
done

echo "[loader] (re)loading $TTL into graph <$GRAPH> ..."
curl -fsS --digest -u "rw:rw" -X PUT \
    -H "Content-Type: text/turtle" \
    --data-binary "@$TTL" \
    "$BASE/sparql-graph-crud-auth?graph-uri=$GRAPH"
echo ""

echo "[loader] triples now in <$GRAPH>:"
curl -fsS "$BASE/sparql" --data-urlencode \
    "query=SELECT (COUNT(*) AS ?n) WHERE { GRAPH <$GRAPH> { ?s ?p ?o } }" \
    -H "Accept: text/csv"
echo "[loader] done."
