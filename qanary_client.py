# Qanary client to test the components
import requests
import logging
import json
from SPARQLWrapper import SPARQLWrapper, JSON

question = "Who is the inventor of the Hawaiian Pizza?"
url = "http://localhost:40111/startquestionansweringwithtextquestion"


logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
headers = {
    "accept": "*/*",
}
body = {
    "question": question,
    "additionaltriples": "",
    "componentfilterinput": "",
    # LD-Java is a Java-implemented component (language detection); the rest are
    # Python. It demonstrates a Java + Python pipeline running together.
    "componentlist[]": ["LD-Java", "NEL-WikidataLookup", "QB-Wikidata", "QE-SparqlExecuter"]
}


logging.info(f"Starting question answering with text question: {question}")
logging.info(f"Sending request to {url}")
logging.info(f"Body: {body}")

response = requests.post(url, headers=headers, data=body)
logging.info(f"Response: {response.json()}")
if response.status_code >= 200 and response.status_code < 300:
    logging.info(
        f"Question answering process completed successfully: {response.json()}")
else:
    logging.error(f"Error: {response.status_code}")
    logging.error(f"Response: {response.text}")
    exit(1)

# pretty print the response
print("Response:")
print(json.dumps(response.json(), indent=4))

response_json = response.json()
endpoint = response_json["endpoint"]
outGraph = response_json["outGraph"]
question_uri = response_json["question"]

logging.info(f"Endpoint: {endpoint}")
logging.info(f"OutGraph: {outGraph}")
logging.info(f"Question URI: {question}")

# get all components
sparql_select_components_that_stored_data = """
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
    SELECT DISTINCT ?component
    FROM <{outGraph}> 
    WHERE {{
        ?annotation oa:annotatedBy ?component .
    }}
""".format(outGraph=outGraph)
# query the triplestore for the stored answer in the outGraph using SparqlWrapper
sparql_wrapper = SPARQLWrapper(endpoint)
sparql_wrapper.setQuery(sparql_select_components_that_stored_data)
sparql_wrapper.setReturnFormat(JSON)
result = sparql_wrapper.query().convert()

logging.info(f"Activated process stored the data in the graph {outGraph}")
java_component_annotations = 0
if "results" in result and "bindings" in result["results"] and len(result["results"]["bindings"]) > 0:
    for bind in result["results"]["bindings"]:
        component = bind["component"]["value"]

        sparql_count_annotations = f"""
            PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
            SELECT (COUNT(?annotation) AS ?numAnnotations)
            FROM <{outGraph}>
            WHERE {{
                ?annotation oa:annotatedBy <{component}> .
            }}
        """
        sparql_wrapper_count = SPARQLWrapper(endpoint)
        sparql_wrapper_count.setQuery(sparql_count_annotations)
        sparql_wrapper_count.setReturnFormat(JSON)
        result_count = sparql_wrapper_count.query().convert()
        num_annotations = 0
        if "results" in result_count and "bindings" in result_count["results"] and len(result_count["results"]["bindings"]) > 0:
            num_annotations = int(
                result_count["results"]["bindings"][0]["numAnnotations"]["value"])
        logging.info(
            f"Component {component} has {num_annotations} annotations in the graph.")
        # the Java component registers as urn:qanary:LD-Java (see LanguageDetector.java)
        if "LD-Java" in component:
            java_component_annotations += num_annotations

# verify the Java-implemented component actually contributed to the pipeline
if java_component_annotations > 0:
    logging.info(
        f"Java component LD-Java contributed {java_component_annotations} annotation(s).")
else:
    logging.error(
        "No annotation by the Java component LD-Java (urn:qanary:LD-Java) found in the graph.")
    exit(1)

    # query the triplestore for the stored answer in the outGraph
sparql_select_to_get_answer_json = """
    PREFIX dbr: <http://dbpedia.org/resource/>
    PREFIX dbo: <http://dbpedia.org/ontology/>
    PREFIX qa: <http://www.wdaqua.eu/qa#>
    PREFIX oa: <http://www.w3.org/ns/openannotation/core/>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?jsonAnswer ?component ?time
    FROM <{outGraph}> 
    WHERE {{
        ?annotationAnswer a qa:AnnotationOfAnswerJson ;
                oa:hasTarget <{question_uri}> ;
                oa:hasBody ?answerJson ;
                oa:annotatedAt ?time ;
                oa:annotatedBy ?component .

                ?answerJson a qa:AnswerJson ;
                    rdf:value ?jsonAnswer .                    
    }}
""".format(outGraph=outGraph, question_uri=question_uri)
logging.info(
    f"SPARQL query to get the stored answer in the outGraph: {sparql_select_to_get_answer_json}")

# query the triplestore for the stored answer in the outGraph using SparqlWrapper
sparql_wrapper = SPARQLWrapper(endpoint)
sparql_wrapper.setQuery(sparql_select_to_get_answer_json)
sparql_wrapper.setReturnFormat(JSON)
result = sparql_wrapper.query().convert()
logging.info(f"Result:\n{json.dumps(result, indent=4)}")
if "results" in result and "bindings" in result["results"] and len(result["results"]["bindings"]) > 0:
    answer = result["results"]["bindings"][0]["jsonAnswer"]["value"]
    component = result["results"]["bindings"][0]["component"]["value"]
    time = result["results"]["bindings"][0]["time"]["value"]
    logging.info(
        f"JSON Answer generated by component {component} at {time}: {json.dumps(answer, indent=4)}")

    # a stored answer may actually be an upstream error payload (e.g. {"error": ...}
    # from rate limiting); treat that as a failed run instead of a silent success
    try:
        parsed_answer = json.loads(answer)
        answer_is_error = isinstance(parsed_answer, dict) and "error" in parsed_answer
    except (json.JSONDecodeError, TypeError):
        answer_is_error = '"error"' in answer
    if answer_is_error:
        logging.error(
            f"Component {component} stored an error payload instead of an answer: {answer}")
        exit(1)
else:
    logging.error(f"No answer found in the triplestore: {result}")
    exit(1)

# pretty print the answer
logging.info(f"Answer: {json.dumps(answer, indent=4)}")
