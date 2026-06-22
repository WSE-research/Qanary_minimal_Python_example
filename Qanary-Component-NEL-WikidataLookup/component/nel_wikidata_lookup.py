import os
import re
import time
import asyncio
import logging
import requests
import urllib.parse

import nltk
from nltk.corpus import stopwords

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from qanary_helpers.qanary_queries import insert_into_triplestore, get_text_question_in_graph


nltk.download('stopwords')
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

if not os.getenv("PRODUCTION"):
    from dotenv import load_dotenv
    load_dotenv()  # required for debugging outside Docker

SERVICE_NAME_COMPONENT = os.environ['SERVICE_NAME_COMPONENT']
MIN_NGRAM = int(os.getenv("MIN_NGRAM", "2"))
MAX_NGRAM = int(os.getenv("MAX_NGRAM", "4"))

# a descriptive User-Agent is required by the Wikimedia User-Agent policy
# (https://meta.wikimedia.org/wiki/User-Agent_policy) and avoids 403/429 throttling
WIKIDATA_USER_AGENT = (
    "Qanary-Minimal-Example/1.0 "
    "(https://github.com/WSE-research/Qanary_minimal_Python_example) python-requests"
)
MAX_RETRIES = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 20

router = APIRouter(
    tags=[SERVICE_NAME_COMPONENT],
    responses={404: {"description": "Not found"}},
)


def _backoff_seconds(attempt: int) -> float:
    return float(min(2 ** attempt, MAX_BACKOFF_SECONDS))


def search_entity(query: str, lang: str = "en", search_limit: int = 3):
    wdt_search_url = "https://www.wikidata.org/w/api.php?action=wbsearchentities&search={search}&format=json&language={lang}&uselang={lang}&type=item&limit={search_limit}"
    # encode the query to handle special characters
    query_encoded = urllib.parse.quote(query)
    wikidata_search_url = wdt_search_url.format(
        search=query_encoded, lang=lang, search_limit=search_limit)
    logging.info(
        f"Wikidata search URL for entity '{query}': {wikidata_search_url}")
    request_headers = {'User-Agent': WIKIDATA_USER_AGENT}

    # retry with exponential backoff so that rate limiting (HTTP 429) or a
    # transient error does not silently drop entity candidates
    response = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(wikidata_search_url,
                                    timeout=REQUEST_TIMEOUT_SECONDS, headers=request_headers)
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = _backoff_seconds(attempt)
                logging.warning(
                    f"Wikidata search connection error: {e}; retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            logging.error(f"Error searching entity {query}: {str(e)}")
            return []

        if response.status_code == 200:
            break
        if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() \
                else _backoff_seconds(attempt)
            logging.warning(
                f"Wikidata search returned HTTP {response.status_code}; retrying in {wait:.1f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        logging.error(
            f"Error searching entity '{query}': HTTP status code {response.status_code} with response {response.text}")
        return []
    else:
        logging.error(f"Error searching entity '{query}': exhausted {MAX_RETRIES} retries")
        return []

    try:
        data = response.json()
    except Exception as e:
        logging.error(f"Error parsing JSON response: {str(e)}")
        return []

    ne_list = []
    for entity in data.get("search", []):
        ne_list.append(f"http://www.wikidata.org/entity/{entity['id']}")
    logging.info(f"Wikidata entities found: {ne_list}")
    return ne_list


def generate_ngrams(text, min_n, max_n):
    stop_words = set(stopwords.words('english'))

    def clean_text(text):
        text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        words = text.split()
        words = [word for word in words if word.lower() not in stop_words]
        return ' '.join(words)

    text = clean_text(text)
    words = text.split()
    ngrams = []
    for n in range(min_n, max_n + 1):
        for i in range(len(words) - n + 1):
            ngrams.append(' '.join(words[i:i+n]))
    return ngrams


@router.get("/")
async def service_description(request: Request):
    # return a JSON response with the name of the component
    return JSONResponse(content={"name": SERVICE_NAME_COMPONENT})


def _entity_annotation_query(graph: str, question_uri: str, entity: str) -> str:
    """SPARQL INSERT storing one recognised entity as a qa:AnnotationOfEntity"""
    return f"""
        PREFIX qa: <http://www.wdaqua.eu/qa#>
        PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        INSERT {{
            GRAPH <{graph}> {{
                ?newAnnotation rdf:type qa:AnnotationOfEntity ;
                    oa:hasBody <{entity}> ;
                    qa:score "1.0"^^xsd:float ;
                    oa:annotatedAt ?time ;
                    oa:annotatedBy <urn:qanary:{SERVICE_NAME_COMPONENT.replace(" ", "-")}> ;
                    oa:hasTarget [
                        a    oa:SpecificResource ;
                        oa:hasSource <{question_uri}> ;
                    ] .
            }}
        }}
        WHERE {{
            BIND (IRI(str(RAND())) AS ?newAnnotation) .
            BIND (now() as ?time)
        }}
    """


@router.post("/annotatequestion")
async def annotate_question(request: Request):
    request_json = await request.json()
    triplestore_endpoint_url = request_json["values"]["urn:qanary#endpoint"]
    triplestore_ingraph_uuid = request_json["values"]["urn:qanary#inGraph"]

    # All triplestore and Wikidata I/O below is blocking. Run it off the event
    # loop via asyncio.to_thread so this worker keeps serving /health and the
    # registration heartbeat while a slow/throttled lookup runs — otherwise the
    # component is marked unhealthy and dropped OFFLINE (and the synchronous
    # pipeline call hangs).
    question = (await asyncio.to_thread(
        get_text_question_in_graph,
        triplestore_endpoint=triplestore_endpoint_url, graph=triplestore_ingraph_uuid))[0]
    question_text, question_uri = question["text"], question["uri"]

    logging.info(f"Querying Wikidata Lookup for question: {question_text}")
    ngrams = generate_ngrams(question_text, MIN_NGRAM, MAX_NGRAM)
    logging.info(f"Generated ngrams: {ngrams}")

    def _collect_entities():
        found = []
        for ngram in ngrams:
            found.extend(search_entity(ngram))
        return found

    entities = await asyncio.to_thread(_collect_entities)
    logging.info(f"Wikidata Lookup response: {entities}")

    if not entities:
        logging.warning(f"No entities found for question: {question_text}")
        return JSONResponse(content=request_json)

    def _store_entities():
        for entity in entities:
            insert_into_triplestore(
                triplestore_endpoint_url,
                _entity_annotation_query(triplestore_ingraph_uuid, question_uri, entity))

    await asyncio.to_thread(_store_entities)

    logging.info(f"Wikidata Lookup completed for question: {question_text}")
    return JSONResponse(content=request_json)


@router.get("/health")
def health():
    return PlainTextResponse(content="alive")
