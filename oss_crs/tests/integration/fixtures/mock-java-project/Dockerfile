FROM ghcr.io/aixcc-finals/base-builder-jvm:v1.3.0

RUN apt-get update && apt-get install -y maven

WORKDIR $SRC/mock_java

COPY fuzz $SRC/src/fuzz
COPY build.sh $SRC/
