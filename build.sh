#!/usr/bin/env bash
# Build the Java component jar before `docker compose up`.
#
# The Java component (qanary-component-LD-Java) depends on the Qanary framework
# (qa.commons / qa.component / qa.qanarycomponent-parent 4.0.0), which is a local
# build and NOT published to Maven Central — so it cannot be resolved from inside
# a clean Docker build container. The framework must therefore already be
# installed in the local Maven repository, e.g. once via:
#   (cd <Qanary>/ && mvn -pl qanary_commons,qanary_component-parent,qanary_component-template -am -DskipTests install)
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
