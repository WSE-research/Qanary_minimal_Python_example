# The jar is built on the host (mvn package) because the Qanary framework
# (qa.component 4.0.0) is a local build, not published to Maven Central — so it
# cannot be resolved from inside a clean build container. See build.sh / the CI
# workflow, which build the jar before `docker compose up`.
FROM eclipse-temurin:25-jre

ARG JAR_FILE=target/qanary-component-ld-java.jar
ADD ${JAR_FILE} /qanary-service.jar

ENTRYPOINT ["java", "--add-opens", "java.base/java.lang=ALL-UNNAMED", "-jar", "/qanary-service.jar"]
