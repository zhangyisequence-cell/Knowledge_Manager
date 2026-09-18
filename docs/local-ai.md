# Local CPU AI service

This is a prepared, opt-in path for running Knowledge Manager text analysis locally. It does not send knowledge content to a cloud model. Nothing in this repository enables AI, downloads a model, builds software, starts a service, or changes the application configuration automatically.

## Pinned sources

The server is built from the official [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp) tag `v0.4.1`, commit [`b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`](https://github.com/ggml-org/llama.cpp/commit/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4).

The model is the official [`Qwen/Qwen2.5-1.5B-Instruct-GGUF`](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) snapshot at revision `91cad51170dc346986eccefdc2dd33a9da36ead9`:

- file: `qwen2.5-1.5b-instruct-q4_k_m.gguf`
- size: `1,117,320,736` bytes
- SHA-256: `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`

The Q4_K_M file is about 1.04 GiB. Its memory use is larger after loading because the server also needs the 8192-token KV cache, compute buffers, and process memory.

## Prepare the binary and model

Run these only on the installed Ubuntu 24.04 server. The build script refuses other operating-system versions, unexpected existing install directories, a mismatched Git commit, non-root execution, or omission of its explicit `--build` switch. It installs build packages with `--no-install-recommends`, compiles only the CPU `llama-server` target, and does not download a model or start a service.

```bash
cd /opt/knowledge-manager/current
sudo bash scripts/build_local_llm.sh --build
/opt/knowledge-manager/llama/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4/bin/llama-server --version
ldd /opt/knowledge-manager/llama/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4/bin/llama-server
```

The build explicitly enables SSE4.2, AVX1, and F16C. It disables AVX2, FMA, BMI2, CPU multi-variant builds, CUDA, HIP, MUSA, Vulkan, SYCL, OpenCL, and RPC. A successful build is not proof that the binary runs on this CPU: `--version` must complete without `Illegal instruction`.

Both `LLAMA_BUILD_UI` and `LLAMA_USE_PREBUILT_UI` are disabled: only the local API is needed. This avoids the upstream build fetching a floating prebuilt web interface even when the source commit is pinned.

At the pinned revision, `LLAMA_CURL` is only a deprecated option name and there is no replacement curl toggle in the server or common CMake targets. The local-file server build does not add libcurl development packages and does not pass the ineffective deprecated option.

The downloader is verification-only unless `--download` is present. It uses `HF_ENDPOINT` when set, resumes through the existing verified downloader, and verifies the full size and SHA-256 before publishing the model. A complete existing file is hashed before reuse.

```bash
sudo install -d -o knowledge-manager -g knowledge-manager -m 0750 \
  /srv/knowledge-manager/model-cache/hub

# Verification only; this fails safely when the model is absent.
sudo -u knowledge-manager /opt/knowledge-manager/current/.venv/bin/python \
  /opt/knowledge-manager/current/scripts/download_local_llm.py \
  --cache-dir /srv/knowledge-manager/model-cache/hub

# This is the explicit public-network download step.
sudo -u knowledge-manager /opt/knowledge-manager/current/.venv/bin/python \
  /opt/knowledge-manager/current/scripts/download_local_llm.py \
  --cache-dir /srv/knowledge-manager/model-cache/hub \
  --download

sha256sum /srv/knowledge-manager/model-cache/hub/models--Qwen--Qwen2.5-1.5B-Instruct-GGUF/snapshots/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

To use a configured mirror, export `HF_ENDPOINT` for the downloader process. Do not substitute a floating branch, model revision, filename, size, or digest.

## Install without enabling

Installing the unit does not enable or start it. The unit also refuses to start unless both the pinned model and `/etc/knowledge-manager/local-ai.enabled` exist.

```bash
sudo install -m 0644 deploy/knowledge-manager-llm.service \
  /etc/systemd/system/knowledge-manager-llm.service
sudo systemctl daemon-reload
systemctl is-enabled knowledge-manager-llm.service || true
systemctl is-active knowledge-manager-llm.service || true
```

The server binds only `127.0.0.1:8080`, runs as `knowledge-manager`, has one request slot, uses two CPU threads, an 8192-token context, Q8 KV caches, a 128-token physical batch, mmap loading, and no mlock. The unit has `Restart=no`; it is not enabled by repository installation.

## Required acceptance before application AI is enabled

First confirm the exact artifacts and CPU behavior:

```bash
grep -F 'commit=b29c606e28a01b1bc8c1351026a0fa6e616bf6c4' \
  /opt/knowledge-manager/llama/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4/.knowledge-manager-llama-build
/opt/knowledge-manager/llama/b29c606e28a01b1bc8c1351026a0fa6e616bf6c4/bin/llama-server --version
```

After those checks, create the local-service opt-in marker and start the service for acceptance. This does not change the Knowledge Manager application's `AI_ENABLED` setting.

```bash
sudo install -d -m 0755 /etc/knowledge-manager
sudo touch /etc/knowledge-manager/local-ai.enabled
sudo systemctl start knowledge-manager-llm.service
sudo systemctl --no-pager --full status knowledge-manager-llm.service
sudo journalctl -u knowledge-manager-llm.service -b --no-pager
curl --fail --silent --show-error http://127.0.0.1:8080/health
curl --fail --silent --show-error http://127.0.0.1:8080/v1/models
```

Then submit a local JSON-only smoke request. Inspect the returned content; HTTP 200 alone is insufficient.

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen2.5-1.5b-instruct-q4-k-m",
    "temperature":0.2,
    "response_format":{"type":"json_object"},
    "messages":[
      {"role":"system","content":"只输出 JSON 对象。"},
      {"role":"user","content":"原文：预算12800元，负责人李明。输出 summary 和 evidence；evidence quote 必须逐字来自原文。"}
    ]
  }'
```

Record resource use during a 3000-Chinese-character request and several consecutive chunks:

```bash
pid="$(systemctl show -p MainPID --value knowledge-manager-llm.service)"
ps -o pid,rss,vsz,etime,cmd -p "${pid}"
free -h
vmstat 1 10
```

Before retaining application AI as enabled, run an actual Knowledge Manager long-document ingestion with unique facts at the beginning, middle, and end. Verify all chunks were processed, JSON fields passed validation, evidence quotes match the saved source character-for-character, source offsets and URL appear in Obsidian, and the job does not silently become a rule summary. Capture elapsed time, peak RSS, swap growth, and any failed jobs.

The application currently enforces a minimum request timeout of 180 seconds and also accepts a larger `ai.timeout_seconds` in its server `config.yaml`. If a real 3000-character chunk takes longer than 180 seconds on this CPU, measure the observed upper bound and increase `ai.timeout_seconds` in the deployed server configuration before acceptance. Do not shorten the input, claim success from a smaller sample, or assume 180 seconds is sufficient without measurement.

Only after those checks pass should the application be configured to use:

- base URL: `http://127.0.0.1:8080/v1`
- model: `qwen2.5-1.5b-instruct-q4-k-m`
- API key: empty
- vision model: unset

Enabling the application remains a separate operator decision. This document deliberately does not edit the existing application configuration.

## Resource and quality boundary

The target has two Ivy Bridge virtual CPUs and a fixed 4 GiB of RAM. The pinned model is expected to fit more plausibly than a 7B model, but it has not yet been measured on the target. Persistent swap growth, an out-of-memory kill, `Illegal instruction`, excessive per-chunk latency, or an unstable service fails acceptance.

Qwen2.5 1.5B is small. Strict JSON syntax does not guarantee correct classification, complete extraction, or faithful evidence. The application rejects malformed fields and filters quotations absent from the source; frequent rejection or empty evidence is a model-quality failure, not proof of success. An 8192-token server context also does not prove every application prompt fits, so real 3000-character chunks must be tested.

Do not fall back to a cloud API while local acceptance is pending. User material must remain local unless the user separately authorizes a cloud provider.
