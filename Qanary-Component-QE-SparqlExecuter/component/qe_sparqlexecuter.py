import os
import json
import time
import asyncio
import logging
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Request
from SPARQLWrapper import SPARQLWrapper, JSON
from fastapi.responses import JSONResponse, PlainTextResponse

from qanary_helpers.qanary_queries import insert_into_triplestore, get_text_question_in_graph, query_triplestore

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

if not os.getenv("PRODUCTION"):
    from dotenv import load_dotenv
    load_dotenv()  # required for debugging outside Docker


SERVICE_NAME_COMPONENT = os.environ['SERVICE_NAME_COMPONENT']
ENDPOINT = os.environ['SPARQL_ENDPOINT']

# the Wikidata Query Service throttles requests with a generic or missing
# User-Agent very aggressively; a descriptive agent is required by the
# Wikimedia User-Agent policy (https://meta.wikimedia.org/wiki/User-Agent_policy)
WIKIDATA_USER_AGENT = (
    "Qanary-Minimal-Example/1.0 "
    "(https://github.com/WSE-research/Qanary_minimal_Python_example) SPARQLWrapper"
)
MAX_RETRIES = int(os.getenv("SPARQL_MAX_RETRIES", "3"))
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = float(os.getenv("SPARQL_MAX_BACKOFF_SECONDS", "8"))
SPARQL_TIMEOUT_SECONDS = float(os.getenv("SPARQL_TIMEOUT_SECONDS", "20"))
# Overall wall-clock budget for execute(). The pipeline calls this component
# synchronously, so an unbounded retry loop would make the whole question-
# answering request (and the frontend's progress indicator) hang indefinitely
# while the public endpoint throttles us. Once the budget is exhausted we return
# an error payload instead of blocking, so the pipeline always returns promptly.
DEADLINE_SECONDS = float(os.getenv("SPARQL_DEADLINE_SECONDS", "40"))

router = APIRouter(
    tags=[SERVICE_NAME_COMPONENT],
    responses={404: {"description": "Not found"}},
)


def _backoff_seconds(attempt: int) -> float:
    return float(min(2 ** attempt, MAX_BACKOFF_SECONDS))


def _retry_delay(error: HTTPError, attempt: int) -> float:
    """honor a numeric Retry-After header if present, otherwise back off exponentially"""
    try:
        retry_after = error.headers.get("Retry-After") if error.headers else None
    except Exception:
        retry_after = None
    if retry_after:
        try:
            # cap it: WDQS can advertise a long Retry-After, but we must not let a
            # single wait blow the synchronous request's time budget
            return min(float(retry_after), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass  # Retry-After may be an HTTP-date; fall back to exponential backoff
    return _backoff_seconds(attempt)


def execute(query: str, endpoint_url: str = ENDPOINT):
    """
    Execute a SPARQL query against the configured endpoint, e.g.
    https://dbpedia.org/sparql
    https://query.wikidata.org/bigdata/namespace/wdq/sparql

    Retries with exponential backoff on rate limiting (HTTP 429) and transient
    server errors so that throttling by the public endpoint does not surface as
    a failed answer for the end user.
    """
    sparql = SPARQLWrapper(endpoint_url, agent=WIKIDATA_USER_AGENT)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    sparql.setTimeout(int(SPARQL_TIMEOUT_SECONDS))

    deadline = time.monotonic() + DEADLINE_SECONDS

    def _sleep_within_budget(wait: float) -> bool:
        """sleep `wait` seconds only if it fits the remaining time budget; returns
        True if it slept, False if the budget is (almost) exhausted"""
        remaining = deadline - time.monotonic()
        if wait >= remaining:
            return False
        time.sleep(wait)
        return True

    for attempt in range(MAX_RETRIES):
        if time.monotonic() >= deadline:
            logging.error("Execute time budget exhausted before completing the query")
            return {'error': "timeout: SPARQL endpoint did not respond within the time budget"}
        try:
            return sparql.query().convert()
        except HTTPError as e:
            status = getattr(e, "code", None)
            if status in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
                wait = _retry_delay(e, attempt)
                logging.warning(
                    f"Endpoint returned HTTP {status}; retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})")
                if _sleep_within_budget(wait):
                    continue
            logging.error(f"Execute HTTP error {status}: {e}")
            return {'error': f"HTTP Error {status}"}
        except URLError as e:
            if attempt < MAX_RETRIES - 1:
                wait = _backoff_seconds(attempt)
                logging.warning(
                    f"Endpoint connection error: {e}; retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})")
                if _sleep_within_budget(wait):
                    continue
            logging.error(f"Execute connection error: {e}")
            return {'error': str(e)}
        except Exception as e:
            e = str(e)
            logging.error(f"Execute error: {e}")
            if 'MalformedQueryException' in e or 'bad formed' in e:
                logging.error(query + str('\n' + e))
            return {'error': e}


@router.get("/")
async def service_description(request: Request):
    # return a JSON response with the name of the component
    return JSONResponse(content={"name": SERVICE_NAME_COMPONENT})


@router.post("/annotatequestion")
async def annotate_question(request: Request):
    request_json = await request.json()
    triplestore_endpoint_url = request_json["values"]["urn:qanary#endpoint"]
    triplestore_ingraph_uuid = request_json["values"]["urn:qanary#inGraph"]

    # Every call below is blocking I/O (triplestore + the public SPARQL endpoint).
    # Run it off the event loop via asyncio.to_thread so this worker keeps serving
    # /health and the registration heartbeat while a slow/throttled query runs —
    # otherwise the component is marked unhealthy and drops OFFLINE.
    question_rows = await asyncio.to_thread(
        get_text_question_in_graph,
        triplestore_endpoint=triplestore_endpoint_url, graph=triplestore_ingraph_uuid)
    question_uri = question_rows[0]['uri']

    sparql = """
    PREFIX qa: <http://www.wdaqua.eu/qa#> 
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/> 
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

    SELECT ?sparql 
    FROM <{uuid}> 
    WHERE {{ 
        ?a rdf:type qa:AnnotationOfAnswerSPARQL .
        OPTIONAL {{ ?a oa:hasBody ?sparql }} 
    }}
    ORDER BY DESC(?score) LIMIT 1
    """.format(uuid=triplestore_ingraph_uuid)

    logging.info(
        f"Querying for already generated SPARQL queries in the Qanary triplestore: {sparql}")
    try:
        result = await asyncio.to_thread(query_triplestore, triplestore_endpoint_url, sparql)
        logging.info(f"Result: {result}")
        if "results" in result and "bindings" in result["results"] and len(result["results"]["bindings"]) > 0:
            generated_sparql = result["results"]["bindings"][0]["sparql"]["value"]
        else:
            logging.warning(f"No SPARQL was generated. Result: {result}")
            return JSONResponse(content=request_json)

        logging.info(f"SPARQL query generated: {generated_sparql}")

        # execute the SPARQL query and return the result to the client
        result = await asyncio.to_thread(execute, generated_sparql, ENDPOINT)
        json_string = json.dumps(result, ensure_ascii=False).replace(
            '\\"', "").replace('"', '\\"')
    except Exception as e:
        logging.warning(f"No SPARQL was generated. Error: {e}")
        return JSONResponse(content=request_json)

    # insert the answer into the triplestore as a JSON answer
    sparql_insert_query = """
    PREFIX dbr: <http://dbpedia.org/resource/>
    PREFIX dbo: <http://dbpedia.org/ontology/>
    PREFIX qa: <http://www.wdaqua.eu/qa#>
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    INSERT {{
    GRAPH <{uuid}> {{
        ?annotationAnswer a qa:AnnotationOfAnswerJson ;
        oa:hasTarget <{question_uri}> ;
        oa:hasBody ?answerJson ;
        oa:annotatedAt ?time ;
        oa:annotatedBy <{component}> .

        ?answerJson a qa:AnswerJson ;
            rdf:value "{json_string}"^^xsd:string  .
            
        qa:AnswerJson rdfs:subClassOf qa:Answer .
        }}
    }}
    WHERE {{
        BIND (IRI(str(RAND())) AS ?annotationAnswer) .
        BIND (IRI(str(RAND())) AS ?answerJson) .
        BIND (now() as ?time) 
    }}
    """.format(
        uuid=triplestore_ingraph_uuid,
        question_uri=question_uri,
        component="urn:qanary:" + SERVICE_NAME_COMPONENT.replace(" ", "-"),
        json_string=json_string)

    # inserting new data to the triplestore
    await asyncio.to_thread(insert_into_triplestore, triplestore_endpoint_url, sparql_insert_query)

    return JSONResponse(content=request_json)


@router.get("/health")
def health():
    return PlainTextResponse(content="alive")
