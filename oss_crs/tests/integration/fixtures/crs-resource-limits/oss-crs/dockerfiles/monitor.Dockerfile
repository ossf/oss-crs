FROM alpine:latest

# Install dependencies (libCRS not needed for this test fixture)
RUN apk add --no-cache bash python3

COPY bin/run_monitor.sh /usr/local/bin/run_monitor.sh
RUN chmod +x /usr/local/bin/run_monitor.sh

ENTRYPOINT ["/usr/local/bin/run_monitor.sh"]
