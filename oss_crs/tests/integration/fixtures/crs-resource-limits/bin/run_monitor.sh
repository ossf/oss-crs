#!/bin/bash
# Dummy monitor - allocates memory in chunks and logs to POV dir

set -e

CONTAINER_NAME="monitor"
CHUNK_SIZE_MB=40
POV_DIR="${OSS_CRS_SUBMIT_DIR:-/tmp}/pov"
LOG_FILE="$POV_DIR/${CONTAINER_NAME}_memory_test.txt"

mkdir -p "$POV_DIR"

log() {
    echo "[$(date -Iseconds)] $1" | tee -a "$LOG_FILE"
}

log "=== $CONTAINER_NAME started ==="
log "cgroup_path: $(cat /proc/self/cgroup 2>/dev/null | head -1)"
log "memory.max: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo 'N/A')"
log "memory.current: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 'N/A')"
log "env_memory_limit: ${OSS_CRS_MEMORY_LIMIT:-not set}"
log "nproc: $(nproc 2>/dev/null || echo 'N/A')"
log "cpuset.cpus.effective: $(cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || echo 'N/A')"
log "env_cpuset: ${OSS_CRS_CPUSET:-not set}"
log ""
log "Will attempt to allocate ${CHUNK_SIZE_MB}MB chunks..."
log ""

# Small delay so containers start at slightly different times
sleep 4

# Python script to allocate memory and report
python3 << 'PYTHON_SCRIPT'
import os
import sys
import time
from datetime import datetime

CHUNK_SIZE = 40 * 1024 * 1024  # 40MB
MAX_CHUNKS = 30
CONTAINER = "monitor"
POV_DIR = os.environ.get("OSS_CRS_SUBMIT_DIR", "/tmp") + "/pov"
LOG_FILE = f"{POV_DIR}/{CONTAINER}_memory_test.txt"

def log(msg):
    ts = datetime.now().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def get_memory_current():
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            return int(f.read().strip())
    except:
        return -1

chunks = []
for i in range(MAX_CHUNKS):
    mem_before = get_memory_current()
    try:
        # Allocate and touch memory to ensure it's actually used
        chunk = bytearray(CHUNK_SIZE)
        for j in range(0, len(chunk), 4096):
            chunk[j] = 0xFF
        chunks.append(chunk)
        mem_after = get_memory_current()
        log(f"ALLOCATED chunk {i+1}: +{CHUNK_SIZE // (1024*1024)}MB, total={len(chunks) * CHUNK_SIZE // (1024*1024)}MB, memory.current={mem_after // (1024*1024)}MB")
        time.sleep(1)  # Give other containers a chance to allocate
    except MemoryError:
        log(f"OOM at chunk {i+1}: could not allocate {CHUNK_SIZE // (1024*1024)}MB")
        log(f"FINAL: allocated {len(chunks)} chunks = {len(chunks) * CHUNK_SIZE // (1024*1024)}MB before OOM")
        break
else:
    log(f"FINAL: allocated all {MAX_CHUNKS} chunks = {MAX_CHUNKS * CHUNK_SIZE // (1024*1024)}MB (no OOM)")

# Keep memory allocated for a bit so other containers can see the pressure
log("Holding memory for 10 seconds...")
time.sleep(10)
log("=== monitor complete ===")
PYTHON_SCRIPT
