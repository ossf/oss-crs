FROM ghcr.io/aixcc-finals/base-builder:v1.3.0

RUN git clone https://github.com/Team-Atlanta/mock-c

WORKDIR $SRC/mock-c

COPY fuzz $SRC/fuzz
COPY build.sh $SRC/
