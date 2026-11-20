# Two final targets:
#   full      (default)  - NVML + CUPTI PM sampling helper + Nsight probe support
#   readonly             - NVML only; counter samplers are not shipped and are hard-disabled
#                          (GPU_INFO_COUNTERS=off). Use on production hosts:
#                          podman build --target readonly -t gpu-info:readonly .
FROM node:20-slim AS web
WORKDIR /app/frontend
RUN npm i -g pnpm@10.23.0
COPY frontend/ ./
RUN rm -rf node_modules dist && CI=true pnpm install && pnpm build

FROM docker.io/nvidia/cuda:12.6.3-devel-ubuntu22.04 AS native
WORKDIR /src/native
RUN apt-get update && apt-get install -y --no-install-recommends cuda-cupti-dev-12-6 && rm -rf /var/lib/apt/lists/*
COPY native/cupti_stream.cpp native/build.sh ./
RUN ./build.sh

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY . .
RUN rm -rf frontend/node_modules native/bin
COPY --from=web /app/frontend/dist frontend/dist
ENV GPU_INFO_HOST=0.0.0.0
EXPOSE 8001 16001
CMD ["uv", "run", "--frozen", "main.py"]

FROM base AS readonly
# Bake read-only mode into the code itself (not an env switch), lock the app tree, verify, and
# run unprivileged. Only /app/data (state + access log) is writable.
RUN printf 'READONLY = True\n' > build_profile.py \
 && rm -f native/build.sh native/build.ps1 native/cupti_stream.cpp \
 && .venv/bin/python -c "import build_profile, main; from fastapi.routing import APIRoute; \
assert build_profile.READONLY; \
bad=[(r.path,sorted(r.methods)) for r in main.app.router.routes if isinstance(r,APIRoute) and set(r.methods)-{'GET','HEAD'}]; \
assert not bad, bad; from activity_probe import counters_disabled; assert counters_disabled(); print('readonly verified')" \
 && mkdir -p data && chown -R root:root /app && chmod -R a-w /app && chown 65534:65534 data && chmod 755 data
ENV GPU_INFO_COUNTERS=off GPU_INFO_ACCESS_LOG=/app/data/access.log UV_NO_SYNC=1
USER 65534:65534
CMD ["/app/.venv/bin/python", "main.py"]

FROM base AS full
COPY --from=native /usr/local/cuda/lib64/libcupti.so* /usr/local/cuda/lib64/libnvperf_*.so /opt/cupti/
COPY --from=native /src/native/bin/cupti_stream native/bin/cupti_stream
ENV GPU_INFO_COUNTERS=on GPU_INFO_CUPTI_PATH=/opt/cupti
