# The jar is built on the host (mvn package, see build.sh and the CI workflow)
# before `docker compose up`; this image only packages it. The Qanary framework
# (qa.component) comes from Maven Central.
FROM eclipse-temurin:25-jre

ARG JAR_FILE=target/qanary-component-ld-java.jar
ADD ${JAR_FILE} /qanary-service.jar

ENTRYPOINT ["java", "--add-opens", "java.base/java.lang=ALL-UNNAMED", "-jar", "/qanary-service.jar"]
