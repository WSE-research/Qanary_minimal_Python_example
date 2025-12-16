import os
import re
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
if os.environ.get('MIN_NGRAM'):
    MIN_NGRAM = int(os.environ['MIN_NGRAM'])
else:
    MIN_NGRAM = 2

if os.environ.get('MAX_NGRAM'):
    MAX_NGRAM = int(os.environ['MAX_NGRAM'])
else:
    MAX_NGRAM = 4

headers = {'Content-Type': 'application/json'}

router = APIRouter(
    tags=[SERVICE_NAME_COMPONENT],
    responses={404: {"description": "Not found"}},
)


def search_entity(query: str, lang: str = "en", search_limit: int = 3):
    wdt_search_url = "https://www.wikidata.org/w/api.php?action=wbsearchentities&search={search}&format=json&language={lang}&uselang={lang}&type=item&limit={search_limit}"
    try:
        # encode the query to handle special characters
        query_encoded = urllib.parse.quote(query)
        wikidata_search_url = wdt_search_url.format(
            search=query_encoded, lang=lang, search_limit=search_limit)
        logging.info(
            f"Wikidata search URL for entity '{query}': {wikidata_search_url}")
        # set agent to avoid 403 errors
        headers = {'User-Agent': 'Mozilla/5.0 (NEL lookup component)'}
        response = requests.get(wikidata_search_url,
                                timeout=20, headers=headers)
        if response.status_code != 200:
            logging.error(
                f"Error searching entity '{query}': HTTP status code {response.status_code} with response {response.text}")
            return []

        # check if the response is valid JSON
        try:
            # log the response
            logging.info(f"Wikidata search response: {response.text}")
            data = response.json()
        except Exception as e:
            logging.error(
                f"Error parsing JSON response: {str(e)}")
            return []

        logging.info(f"Wikidata search response: {response.json()}")
        data = response.json()
        ne_list = []
        for entity in data["search"]:
            wdt_id = entity["id"]
            ne_list.append(f"http://www.wikidata.org/entity/{wdt_id}")
        logging.info(f"Wikidata entities found: {ne_list}")
        return ne_list
    except Exception as e:
        logging.error(f"Error searching entity {query}: {str(e)}")
        return []


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
async def qanary_service(request: Request):
    # return a JSON response with the name of the component
    return JSONResponse(content={"name": SERVICE_NAME_COMPONENT})


@router.post("/annotatequestion")
async def qanary_service(request: Request):
    request_json = await request.json()
    triplestore_endpoint_url = request_json["values"]["urn:qanary#endpoint"]
    triplestore_ingraph_uuid = request_json["values"]["urn:qanary#inGraph"]

    # get question text from triplestore
    question_text = get_text_question_in_graph(
        triplestore_endpoint_url, triplestore_ingraph_uuid)[0]['text']
    question_uri = get_text_question_in_graph(
        triplestore_endpoint=triplestore_endpoint_url, graph=triplestore_ingraph_uuid)[0]['uri']

    logging.info(f"Querying Wikidata Lookup for question: {question_text}")

    ngrams = generate_ngrams(question_text, MIN_NGRAM, MAX_NGRAM)

    logging.info(f"Generated ngrams: {ngrams}")

    entities = []
    for ngram in ngrams:
        entities.extend(search_entity(ngram))

    logging.info(f"Wikidata Lookup response: {entities}")

    if len(entities) == 0:
        logging.warning(f"No entities found for question: {question_text}")
        return JSONResponse(content=request_json)

    for entity in entities:

        sparql_insert_query = f"""
                        PREFIX dbr: <http://dbpedia.org/resource/>
                        PREFIX dbo: <http://dbpedia.org/ontology/>
                        PREFIX qa: <http://www.wdaqua.eu/qa#>
                        PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
                        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
                        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
                        INSERT {{
                        GRAPH <{triplestore_ingraph_uuid}> {{
                            ?newAnnotation rdf:type qa:AnnotationOfEntity ;
                                oa:hasBody <{entity}> ;
                                qa:score \"1.0\"^^xsd:float ;
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
        # inserting new data to the triplestore
        insert_into_triplestore(triplestore_endpoint_url, sparql_insert_query)

    logging.info(f"Wikidata Lookup completed for question: {question_text}")
    return JSONResponse(content=request_json)


@router.get("/health")
def health():
    return PlainTextResponse(content="alive")
