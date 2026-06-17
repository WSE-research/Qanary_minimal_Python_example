package eu.wdaqua.qanary.component.ldjava;

import org.springframework.boot.test.context.SpringBootTest;

import eu.wdaqua.qanary.component.AbstractSwaggerUiAvailabilityTest;

/**
 * Ensures this component exposes the Swagger UI via /swagger-ui, /swagger, /openapi
 * and /docs and the OpenAPI description at /api-docs. The behaviour is provided
 * centrally by the qa.component framework; this test (and the shared base it extends)
 * guards against a component being rebuilt against a framework jar without springdoc.
 */
@SpringBootTest(classes = Application.class, webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SwaggerUiAvailabilityTest extends AbstractSwaggerUiAvailabilityTest {
}
