#!/usr/bin/env python3
"""Offline, deterministic end-to-end test for the Qanary minimal Python example.

For every predefined case in ``testcases.json`` this script:

  1. waits until the pipeline, the requested components and the local
     Wikidata-subset dataset are ready,
  2. sends the question through the pipeline
     (``/startquestionansweringwithtextquestion`` with the LD-Java + Python
     component list),
  3. reads the JSON answer the pipeline stored in the result graph, and
  4. asserts that the answer is not an error payload, that the Java component
     (LD-Java) contributed, and that every expected fact is present.

Because ``QE-SparqlExecuter`` is pointed at the bundled local Wikidata subset
(see ``Wikidata-dataset/``), the answers are deterministic and require no public
Wikidata Query Service — so this doubles as an offline regression test.

It is driven both by CI (``run-e2e-tests.sh`` brings the stack up first) and by
hand against an already-running ``docker compose`` stack:

    python3 e2e/e2e_test.py

Configuration (environment variables):
    QANARY_PIPELINE_URL      default http://localhost:40111
    DATASET_SPARQL_ENDPOINT  default http://localhost:8891/sparql
    READINESS_TIMEOUT        seconds to wait for readiness, default 240
    E2E_TESTCASES            path to the test-case file, default ./testcases.json

Exits 0 if all cases pass, 1 otherwise. Requires only the ``requests`` package.
"""
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE_URL = os.environ.get("QANARY_PIPELINE_URL", "http://localhost:40111").rstrip("/")
DATASET_SPARQL_ENDPOINT = os.environ.get("DATASET_SPARQL_ENDPOINT", "http://localhost:8891/sparql")
READINESS_TIMEOUT = int(os.environ.get("READINESS_TIMEOUT", "240"))
TESTCASES_FILE = os.environ.get("E2E_TESTCASES", os.path.join(HERE, "testcases.json"))
# entity linking (NEL) calls the live Wikidata search API, so a case can
# occasionally fail transiently; retry a few times before declaring failure
ATTEMPTS = int(os.environ.get("E2E_ATTEMPTS", "3"))
RETRY_DELAY = int(os.environ.get("E2E_RETRY_DELAY", "5"))

# the test dataset is built around this entity (Hawaiian pizza); used as a
# readiness probe to confirm the subset was loaded into the dataset triplestore
DATASET_PROBE_ENTITY = "http://www.wikidata.org/entity/Q590076"

SESSION = requests.Session()


def log(msg):
    print(msg, flush=True)


def sparql_query(endpoint, query, timeout=30):
    """Run a SPARQL query against a Virtuoso /sparql endpoint and return JSON."""
    response = SESSION.post(
        endpoint,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
# readiness
# --------------------------------------------------------------------------- #
def _wait_for(label, predicate, timeout):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            if predicate():
                log(f"  ✓ ready: {label}")
                return True
        except Exception as exc:  # noqa: BLE001 - report and keep polling
            last_error = exc
        time.sleep(3)
    log(f"  ✗ timeout after {timeout}s waiting for {label} (last: {last_error})")
    return False


def _pipeline_healthy():
    response = SESSION.get(f"{PIPELINE_URL}/actuator/health", timeout=10)
    return response.ok and response.json().get("status") == "UP"


def _components_registered(required):
    response = SESSION.get(f"{PIPELINE_URL}/applications", headers={"Accept": "application/json"}, timeout=10)
    response.raise_for_status()
    up = {
        app["name"]
        for app in response.json()
        if (app.get("status") or app.get("statusInfo", {}).get("status")) == "UP"
    }
    missing = [name for name in required if name not in up]
    if missing:
        raise RuntimeError(f"not yet UP: {missing} (UP now: {sorted(up)})")
    return True


def _dataset_loaded():
    result = sparql_query(DATASET_SPARQL_ENDPOINT, f"ASK {{ <{DATASET_PROBE_ENTITY}> ?p ?o }}", timeout=10)
    return bool(result.get("boolean"))


def wait_until_ready(required_components):
    log(f"Waiting for the stack to become ready (timeout {READINESS_TIMEOUT}s each)...")
    return all([
        _wait_for(f"pipeline at {PIPELINE_URL}", _pipeline_healthy, READINESS_TIMEOUT),
        _wait_for(f"local dataset at {DATASET_SPARQL_ENDPOINT}", _dataset_loaded, READINESS_TIMEOUT),
        _wait_for(f"components {required_components}", lambda: _components_registered(required_components), READINESS_TIMEOUT),
    ])


# --------------------------------------------------------------------------- #
# running a question and reading its answer
# --------------------------------------------------------------------------- #
def run_question(question, components):
    body = [("question", question), ("additionaltriples", ""), ("componentfilterinput", "")]
    body += [("componentlist[]", component) for component in components]
    response = SESSION.post(
        f"{PIPELINE_URL}/startquestionansweringwithtextquestion",
        data=body,
        headers={"accept": "*/*"},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()


_PREFIXES = """
    PREFIX qa: <http://www.wdaqua.eu/qa#>
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""


def get_annotating_components(endpoint, graph):
    query = _PREFIXES + f"""
        SELECT DISTINCT ?component FROM <{graph}>
        WHERE {{ ?annotation oa:annotatedBy ?component . }}
    """
    bindings = sparql_query(endpoint, query)["results"]["bindings"]
    return [b["component"]["value"] for b in bindings]


def get_json_answer(endpoint, graph, question_uri):
    query = _PREFIXES + f"""
        SELECT ?jsonAnswer ?component FROM <{graph}>
        WHERE {{
            ?annotationAnswer a qa:AnnotationOfAnswerJson ;
                oa:hasTarget <{question_uri}> ;
                oa:hasBody ?answerJson ;
                oa:annotatedBy ?component .
            ?answerJson a qa:AnswerJson ;
                rdf:value ?jsonAnswer .
        }}
    """
    bindings = sparql_query(endpoint, query)["results"]["bindings"]
    if not bindings:
        return None, None
    return bindings[0]["jsonAnswer"]["value"], bindings[0]["component"]["value"]


def answer_is_error_payload(answer):
    try:
        parsed = json.loads(answer)
        return isinstance(parsed, dict) and "error" in parsed
    except (json.JSONDecodeError, TypeError):
        return '"error"' in answer


# --------------------------------------------------------------------------- #
# a single test case
# --------------------------------------------------------------------------- #
def run_case(case, default_components):
    components = case.get("components", default_components)
    question = case["question"]
    expected = case.get("expected_contains", [])
    log(f"\n=== case '{case['name']}': {question!r}")

    response = run_question(question, components)
    endpoint = response["endpoint"]
    out_graph = response["outGraph"]
    question_uri = response["question"]
    log(f"    outGraph={out_graph}")

    failures = []

    # the Java component must have contributed (this is a Java+Python pipeline)
    annotators = get_annotating_components(endpoint, out_graph)
    if not any("LD-Java" in a for a in annotators):
        failures.append(f"Java component LD-Java did not annotate (annotators: {annotators})")

    answer, answer_component = get_json_answer(endpoint, out_graph, question_uri)
    if answer is None:
        failures.append("no JSON answer was stored in the result graph")
    else:
        log(f"    answer stored by {answer_component}")
        if answer_is_error_payload(answer):
            failures.append(f"answer is an error payload: {answer[:200]}")
        else:
            haystack = answer.lower()
            for needle in expected:
                if needle.lower() not in haystack:
                    failures.append(f"expected {needle!r} not found in the answer")

    if failures:
        for f in failures:
            log(f"    ✗ {f}")
        return False
    log(f"    ✓ passed ({len(expected)} expected fact(s) present)")
    return True


def main():
    with open(TESTCASES_FILE, encoding="utf-8") as fh:
        spec = json.load(fh)
    default_components = spec["default_components"]
    cases = spec["cases"]
    log(f"Loaded {len(cases)} test case(s) from {TESTCASES_FILE}")
    log(f"Pipeline: {PIPELINE_URL} | dataset: {DATASET_SPARQL_ENDPOINT}")

    if not wait_until_ready(default_components):
        log("\nFAILED: the stack did not become ready in time.")
        return 1

    results = {}
    for case in cases:
        ok = False
        for attempt in range(1, ATTEMPTS + 1):
            if attempt > 1:
                log(f"    retry {attempt}/{ATTEMPTS} after {RETRY_DELAY}s ...")
                time.sleep(RETRY_DELAY)
            try:
                ok = run_case(case, default_components)
            except Exception as exc:  # noqa: BLE001 - a transient HTTP error is retryable
                log(f"    ✗ error: {exc}")
                ok = False
            if ok:
                break
        results[case["name"]] = ok

    log("\n===== SUMMARY =====")
    for name, ok in results.items():
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(1 for ok in results.values() if ok)
    log(f"{passed}/{len(results)} case(s) passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
