package eu.wdaqua.qanary.component.ldjava;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import eu.wdaqua.qanary.commons.QanaryMessage;
import eu.wdaqua.qanary.commons.QanaryQuestion;
import eu.wdaqua.qanary.commons.QanaryUtils;
import eu.wdaqua.qanary.commons.triplestoreconnectors.QanaryTripleStoreConnector;
import eu.wdaqua.qanary.component.QanaryComponent;

/**
 * A minimal Java Qanary component: it annotates the question with its language as
 * a {@code qa:AnnotationOfQuestionLanguage}. The detection here is intentionally
 * trivial (a placeholder returning "en") — the point of this component is to
 * demonstrate a Java component cooperating with the Python components in one
 * pipeline, not sophisticated language detection.
 */
public class LanguageDetector extends QanaryComponent {

    private static final Logger logger = LoggerFactory.getLogger(LanguageDetector.class);

    private final String applicationName;

    public LanguageDetector(String applicationName) {
        this.applicationName = applicationName;
    }

    /** placeholder detector — replace with a real language detector as needed */
    private String detectLanguage(String questionText) {
        return "en";
    }

    @Override
    public QanaryMessage process(QanaryMessage myQanaryMessage) throws Exception {
        logger.info("process: {}", myQanaryMessage);

        QanaryUtils utils = this.getUtils();
        QanaryTripleStoreConnector connector = utils.getQanaryTripleStoreConnector();
        QanaryQuestion<String> myQanaryQuestion = this.getQanaryQuestion();

        String graph = myQanaryQuestion.getInGraph().toASCIIString();
        String questionUri = myQanaryQuestion.getUri().toASCIIString();

        String questionText;
        try {
            questionText = myQanaryQuestion.getTextualRepresentation();
        } catch (Exception e) {
            questionText = null; // detection falls back to the default below
        }
        String language = detectLanguage(questionText);

        // store the detected language as a standard Qanary annotation
        String insert = "" //
                + "PREFIX qa: <http://www.wdaqua.eu/qa#> " //
                + "PREFIX oa: <http://www.w3.org/ns/openannotation/core/> " //
                + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> " //
                + "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> " //
                + "INSERT { GRAPH <" + graph + "> { " //
                + "  ?annotation rdf:type qa:AnnotationOfQuestionLanguage ; " //
                + "     oa:hasTarget <" + questionUri + "> ; " //
                + "     oa:hasBody \"" + language + "\"^^xsd:string ; " //
                + "     oa:annotatedBy <urn:qanary:" + applicationName.replace(" ", "-") + "> ; " //
                + "     oa:annotatedAt ?time . " //
                + "} } WHERE { " //
                + "  BIND (IRI(CONCAT(\"urn:qanary:annotation:language:\", STR(RAND()))) AS ?annotation) . " //
                + "  BIND (now() AS ?time) . " //
                + "}";

        connector.update(insert);
        logger.info("annotated question language '{}' for {}", language, questionUri);

        return myQanaryMessage;
    }
}
