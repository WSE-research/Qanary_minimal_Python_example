#!/usr/bin/env bash
# Build the Java component jar before `docker compose up`.
#
# The Java component (qanary-component-LD-Java) depends on the Qanary framework
# (qa.component, version qanary.version in its pom.xml), which Maven resolves from
# Maven Central. The jar is built here on the host and only copied into the
# Docker image (see qanary-component-LD-Java/Dockerfile).
#
# The Python components need no build step here (they are built from source by
# docker compose). This mirrors ../Qanary_minimal_Java_Python_example/build.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${JAVA_HOME:=/home/both/jdk21}"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

echo "Building qanary-component-LD-Java with: $(java -version 2>&1 | head -1)"
mvn -B --no-transfer-progress -DskipTests -f "$HERE/qanary-component-LD-Java/pom.xml" clean package
echo "Built: $HERE/qanary-component-LD-Java/target/qanary-component-ld-java.jar"
