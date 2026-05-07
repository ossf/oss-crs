FROM ghcr.io/aixcc-finals/base-builder-jvm:v1.3.0

RUN apt-get update && apt-get install -y maven

RUN git clone https://github.com/Team-Atlanta/mock-java mock_java

WORKDIR $SRC/mock_java

COPY fuzz $SRC/fuzz
COPY build.sh $SRC/
