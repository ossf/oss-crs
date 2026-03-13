#!/bin/bash
# Analyze memory test results from POV files
#
# Usage: ./analyze_results.sh <path-to-pov-dir>
#
# Example:
#   ./analyze_results.sh .oss-crs-workdir/.../SUBMIT_DIR/pov/

POV_DIR="${1:-.}"

echo "=== Memory Allocation Test Results ==="
echo "POV Directory: $POV_DIR"
echo ""

total_allocated=0

for f in "$POV_DIR"/*_memory_test.txt; do
    if [ -f "$f" ]; then
        container=$(basename "$f" | sed 's/_memory_test.txt//')
        echo "--- $container ---"

        # Show cgroup info
        grep -E "(cgroup_path|memory\.max|env_memory)" "$f" | head -4
        echo ""

        # Show allocation attempts
        grep -E "(ALLOCATED|OOM|FINAL)" "$f"
        echo ""

        # Extract final allocation
        final=$(grep "FINAL:" "$f" | grep -oP '\d+(?=MB before OOM|MB \(no OOM\))' | tail -1)
        if [ -n "$final" ]; then
            total_allocated=$((total_allocated + final))
        fi
    fi
done

echo "=== Summary ==="
echo "Total allocated across all containers: ${total_allocated}MB"
echo ""
echo "Expected results:"
echo "  Without --cgroup-parent: Each container ~80-120MB, total ~240-360MB"
echo "  With --cgroup-parent:    Combined total ~80-120MB (shared 128MB limit)"
