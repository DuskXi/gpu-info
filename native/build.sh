#!/usr/bin/env bash
# Linux counterpart of build.ps1: builds native/bin/cupti_stream against a CUDA toolkit.
# CUPTI lives in extras/CUPTI for runfile installs and in lib64/include for distro packages.
set -euo pipefail
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda}"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$CUDA_ROOT/extras/CUPTI/include/cupti_pmsampling.h" ]]; then
    CUPTI_INC="$CUDA_ROOT/extras/CUPTI/include"; CUPTI_LIB="$CUDA_ROOT/extras/CUPTI/lib64"
else
    CUPTI_INC="$CUDA_ROOT/include"; CUPTI_LIB="$CUDA_ROOT/lib64"
fi
mkdir -p "$HERE/bin"
g++ -std=c++17 -O2 -I"$CUDA_ROOT/include" -I"$CUPTI_INC" "$HERE/cupti_stream.cpp" \
    -o "$HERE/bin/cupti_stream" -L"$CUDA_ROOT/lib64/stubs" -L"$CUPTI_LIB" -lcuda -lcupti -lpthread
