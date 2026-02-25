#!/bin/bash
# Simple compile script that runs OSS-Fuzz compile.sh and copies output to BUILD_OUT_DIR
set -e

# Run the standard compile
compile

# Copy compiled binaries to build output
libCRS submit-build-output /out build
