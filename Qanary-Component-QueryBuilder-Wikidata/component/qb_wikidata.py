import os
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from qanary_helpers.qanary_queries import insert_into_triplestore, get_text_question_in_graph, query_triplestore


logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

if not os.getenv("PRODUCTION"):
    from dotenv import load_dotenv
    load_dotenv()  # required for debugging outside Docker

SERVICE_NAME_COMPONENT = os.environ['SERVICE_NAME_COMPONENT']

router = APIRouter(
    tags=[SERVICE_NAME_COMPONENT],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def service_description(request: Request):
    # return a JSON response with the name of the component
    return JSONResponse(content={"name": SERVICE_NAME_COMPONENT})


def _wikidata_answer_query(entity: str) -> str:
    """The SPARQL query (for the public Wikidata endpoint) that retrieves the
    answer for a linked entity. Stored as the generated answer query so the
    QE-SparqlExecuter component can run it."""
    return f"""
        PREFIX wikibase: <http://wikiba.se/ontology#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?label ?pLabel ?oLabel WHERE {{
            <{entity}> rdfs:label ?label .
            <{entity}> ?p ?o .
            ?o rdfs:label ?oLabel .
            ?prop wikibase:directClaim ?p ;
                rdfs:label ?pLabel .
            FILTER(LANG(?pLabel) = 'en')
            FILTER(LANG(?oLabel) = 'en')
            FILTER(LANG(?label) = 'en')
        }}
    """.replace("\n", " ").strip()


def _answer_sparql_annotation_query(graph: str, question_uri: str, answer_sparql: str) -> str:
    """SPARQL INSERT storing the generated answer query as qa:AnnotationOfAnswerSPARQL"""
    return f"""
        PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
        PREFIX qa: <http://www.wdaqua.eu/qa#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        INSERT {{
            GRAPH <{graph}> {{
                ?newAnnotation rdf:type qa:AnnotationOfAnswerSPARQL ;
                    oa:hasTarget <{question_uri}> ;
                    oa:hasBody "{answer_sparql}" ;
                    qa:score "1.0"^^xsd:float ;
                    oa:annotatedAt ?time ;
                    oa:annotatedBy <urn:qanary:{SERVICE_NAME_COMPONENT.replace(" ", "-")}> .
            }}
        }}
        WHERE {{
            BIND (IRI(CONCAT("urn:qanary:annotation:answer:sparql:", STR(RAND()))) AS ?newAnnotation) .
            BIND (now() as ?time) .
        }}
    """


@router.post("/annotatequestion")
async def annotate_question(request: Request):
    request_json = await request.json()
    triplestore_endpoint_url = request_json["values"]["urn:qanary#endpoint"]
    triplestore_ingraph_uuid = request_json["values"]["urn:qanary#inGraph"]

    # All triplestore I/O below is blocking. Run it off the event loop via
    # asyncio.to_thread so /health and the registration heartbeat stay responsive
    # while it runs (consistent with the NEL/QE components).
    question_uri = (await asyncio.to_thread(
        get_text_question_in_graph,
        triplestore_endpoint=triplestore_endpoint_url, graph=triplestore_ingraph_uuid))[0]["uri"]

    entity_query = f"""
    PREFIX qa: <http://www.wdaqua.eu/qa#>
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
    SELECT ?entity
    FROM <{triplestore_ingraph_uuid}>
    WHERE {{
        ?a a qa:AnnotationOfEntity ;
        OPTIONAL {{ ?a oa:hasBody ?entity }}
    }}
    ORDER BY DESC(?score) LIMIT 1
    """

    logging.info(f"Querying for entities: {entity_query}")
    entity_result = await asyncio.to_thread(query_triplestore, triplestore_endpoint_url, entity_query)
    entity_list = [bind["entity"]["value"] for bind in entity_result["results"]["bindings"]]
    logging.info(f"Entity candidates: {entity_list}")

    def _store_answer_queries():
        for candidate in entity_list:
            answer_sparql = _wikidata_answer_query(candidate)
            insert_query = _answer_sparql_annotation_query(
                triplestore_ingraph_uuid, question_uri, answer_sparql)
            logging.debug(f"SPARQL for query candidates:\n{insert_query}")
            insert_into_triplestore(triplestore_endpoint_url, insert_query)

    await asyncio.to_thread(_store_answer_queries)

    return JSONResponse(content=request_json)


@router.get("/health")
def health():
    return PlainTextResponse(content="alive")
