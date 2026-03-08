#!/bin/bash
# Dummy build script - logs memory info to build output

set -e

echo "=== Dummy CRS Builder ==="
echo "Container: builder"
echo "Time: $(date -Iseconds)"
echo ""

# Log cgroup info
echo "=== Cgroup Info ==="
echo "Cgroup path:"
cat /proc/self/cgroup 2>/dev/null || echo "N/A"
echo ""

# Log memory limits
echo "=== Memory Limits ==="
if [ -f /sys/fs/cgroup/memory.max ]; then
    MEM_MAX=$(cat /sys/fs/cgroup/memory.max)
    echo "memory.max: $MEM_MAX"
    if [ "$MEM_MAX" != "max" ]; then
        echo "memory.max (human): $(numfmt --to=iec $MEM_MAX 2>/dev/null || echo $MEM_MAX)"
    fi
else
    echo "memory.max: N/A (cgroup v1 or not available)"
fi

if [ -f /sys/fs/cgroup/memory.current ]; then
    MEM_CURRENT=$(cat /sys/fs/cgroup/memory.current)
    echo "memory.current: $MEM_CURRENT ($(numfmt --to=iec $MEM_CURRENT 2>/dev/null || echo $MEM_CURRENT))"
fi
echo ""

# Log CPU info
echo "=== CPU Info ==="
if [ -f /sys/fs/cgroup/cpuset.cpus.effective ]; then
    echo "cpuset.cpus.effective: $(cat /sys/fs/cgroup/cpuset.cpus.effective)"
elif [ -f /sys/fs/cgroup/cpuset.cpus ]; then
    echo "cpuset.cpus: $(cat /sys/fs/cgroup/cpuset.cpus)"
else
    echo "cpuset: N/A"
fi
echo ""

# Log environment
echo "=== Environment ==="
echo "OSS_CRS_BUILD_OUT_DIR: ${OSS_CRS_BUILD_OUT_DIR:-not set}"
echo "OSS_CRS_CPUSET: ${OSS_CRS_CPUSET:-not set}"
echo "OSS_CRS_MEMORY_LIMIT: ${OSS_CRS_MEMORY_LIMIT:-not set}"
echo ""

# Write output to build directory
OUTPUT_DIR="${OSS_CRS_BUILD_OUT_DIR:-/tmp}"
mkdir -p "$OUTPUT_DIR"

{
    echo "build_time: $(date -Iseconds)"
    echo "container: builder"
    echo "memory_max: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo 'N/A')"
    echo "memory_current: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 'N/A')"
    echo "cpuset: $(cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || cat /sys/fs/cgroup/cpuset.cpus 2>/dev/null || echo 'N/A')"
    echo "cgroup: $(cat /proc/self/cgroup 2>/dev/null | head -1 || echo 'N/A')"
} > "$OUTPUT_DIR/build_info.txt"

echo "=== Build Complete ==="
echo "Output written to: $OUTPUT_DIR/build_info.txt"

# For dummy CRS, we just need the build_info.txt to exist in BUILD_OUT_DIR
# No need to submit - the file is already in the mounted volume
