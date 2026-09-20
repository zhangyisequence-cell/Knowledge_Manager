# Remote Obsidian, WeChat, and DeepSeek Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已验证的 Knowledge Manager 扩展为可由 Windows 与安卓 Obsidian 双向同步、通过 Cloudflare Tunnel 接收真实微信客服消息、并通过 DeepSeek API 完成有证据约束的归类、总结和提取的 Ubuntu 服务。

**Architecture:** 保留现有本地解析、OCR、Whisper 转写、持久化队列和 Obsidian 写入。新增 DeepSeek OpenAI 兼容提供方的长文证据校验、Ubuntu 上由 Tailscale 保护的 Syncthing Vault 同步、Cloudflare Tunnel 的单路径微信回调，以及会暂停所有写入者的分层备份保留。公网回调、个人同步和管理面保持独立。

**Tech Stack:** Python 3、FastAPI、httpx、Pydantic、SQLite、systemd、Tailscale、Syncthing、cloudflared、Obsidian、DeepSeek `deepseek-chat` API。

**Spec:** `docs/superpowers/specs/2026-09-19-remote-obsidian-wechat-deepseek-design.md`

## Global Constraints

- 所有原件、提取正文、转写、任务状态、API 结果和 Obsidian 笔记的完整主数据保存在 Ubuntu `/srv/knowledge-manager`。
- 原始二进制文件不发送给 DeepSeek；正文、表格文本、OCR 文本和音视频转写文本按用户批准发送给 DeepSeek API。
- `OPENAI_API_KEY`、微信密钥、Cloudflare 凭据、Tailscale 密钥、Syncthing 设备密钥和真实知识不得进入 Git、日志或测试输出。
- DeepSeek 生产配置使用 `KNOWLEDGE_OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-chat`，实际模型标识在真实验收前锁定。
- AI 结果必须通过 JSON、分段覆盖、原文证据和来源偏移校验；分段失败不得静默标记成功。
- Obsidian 只同步 `/srv/knowledge-manager/vault`；数据库、队列、配置、模型缓存和备份归档不进入同步文件夹。
- Syncthing 管理面、SSH、管理 API、Vault 和数据库不经公网暴露；Cloudflare 只转发 `/wechat/callback`。
- 备份开始前必须暂停微信回调、Syncthing 和主应用；完成或可处理取消后只恢复备份前正在运行的服务。
- 每小时归档、30 天 Syncthing 版本、24 个小时点、30 个日点和 12 个周点评级保留；第二物理盘副本必须核对 SHA-256。
- 只有真实微信、Windows、安卓、DeepSeek、重启和恢复证据全部通过，才能把系统标为完成。

## Review Focus

- DeepSeek 返回截断 JSON、错误 `finish_reason` 或引用不存在的证据时，任务必须失败并保留原件；由 Task 1 的 API 合同测试覆盖。
- 文档开头、中间或结尾分段请求失败时，不能生成看似完整的摘要；由 Task 1 的连续覆盖测试覆盖。
- Syncthing 设备离线编辑同一 Markdown 时，服务器必须保留冲突文件和历史版本；由 Task 3 的文件级验收覆盖。
- 备份在 Syncthing 正在写入、被取消或服务原本关闭时，必须保持一致性且不错误启动服务；由 Task 2 的 systemd 夹具测试覆盖。
- 微信回调重复、未授权发送者、消息分页为空但 `has_more=1`、Cloudflare 访问管理路径时，必须分别幂等、拒绝、继续翻页和返回 404；由 Task 4 的协议与入口测试覆盖。

---

### Task 1: DeepSeek 提供方与有证据的 AI 汇总

**Files:**
- Modify: `vendor/knowledge-inbox/backend/config.py`
- Modify: `vendor/knowledge-inbox/backend/processors/ai.py`
- Modify: `deploy/server.env.example`
- Modify: `scripts/run_local.py`
- Create: `tests/test_deepseek_ai.py`
- Modify: `tests/test_check_local_ai_ingestion.py` (rename assertions and labels from local-model wording to provider-neutral wording)
- Modify: `docs/local-ai.md` (mark the former local LLM path archived and disabled)

**Interfaces:**
- Consumes: existing `AIConfig`, `ContentItem`, `TextChunk`, and `AIProcessor.analyze(item)`.
- Produces: `AIProcessor.analyze(item) -> dict[str, Any]` with `analysis_mode`, `analysis_provider`, `model`, `coverage`, `evidence`, `complete`, and token/error metadata; callers continue to receive the existing `ContentItem` fields.

- [ ] **Step 1: Write failing API-contract tests.**

  Add an `httpx.MockTransport` fixture and assert that `AIProcessor` posts to `/chat/completions` with `model="deepseek-chat"`, `response_format.type="json_object"`, the configured bearer token, and only extracted text. Assert that a raw media path or binary bytes never occur in the JSON body. Add tests for valid JSON, missing required fields, invalid evidence quotes, non-stop `finish_reason`, HTTP 401, HTTP 429, and a timeout.

  Add a long fixture with three unique markers at the beginning, middle, and end. Return one valid response per chunk, make the middle request fail once, and assert the first attempt raises a durable retry error without writing a completed item. On a retry, assert all chunks are represented in `coverage` and evidence offsets point into the original text.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

  Run `python -m pytest tests/test_deepseek_ai.py -q`. Expected result: the new provider/error and coverage assertions fail against the current local-model assumptions.

- [ ] **Step 3: Implement configuration and provider behavior.**

  Extend `AIConfig` with provider/error metadata without changing the existing caller signature. Read `KNOWLEDGE_OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, and `AI_ENABLED` exactly once in `get_config()`. Set the example configuration to `https://api.deepseek.com`, `deepseek-chat`, and disabled-by-default AI. Keep API key values out of `repr`, exception text, and logs.

  In `AIProcessor`, retain the 3,000-character source offsets, but add `_request_chunk_with_retry()` with bounded exponential backoff for timeout, connection, 429, and 5xx responses. Treat 401, 402/insufficient balance, 400, malformed JSON, missing fields, invalid evidence, and non-stop output as terminal task errors. Validate every `evidence.quote` with `chunk.text.find()` and calculate absolute `start`/`end` offsets. Remove the current binary video/image payload branches from the DeepSeek path: production requests contain extracted text only, and `media_files` paths must never be read or base64-encoded for this provider.

  Add `_request_summary()` that sends only validated chunk facts and evidence to the same API, then validates the returned summary/category/tags against the evidence set. The aggregate result must report `processed_chunks == total_chunks`, `complete=true`, `analysis_mode="ai"`, `analysis_provider="deepseek"`, and the exact model string. Never fall back to a successful rules result when AI is enabled and a request fails.

- [ ] **Step 4: Run the focused tests and confirm they pass.**

  Run `python -m pytest tests/test_deepseek_ai.py tests/test_check_local_ai_ingestion.py -q`. Expected result: all provider, coverage, evidence, error-redaction, and compatibility tests pass.

- [ ] **Step 5: Commit the provider slice.**

  ```bash
  git add vendor/knowledge-inbox/backend/config.py vendor/knowledge-inbox/backend/processors/ai.py deploy/server.env.example scripts/run_local.py tests/test_deepseek_ai.py tests/test_check_local_ai_ingestion.py docs/local-ai.md
  git commit -m "feat: add grounded DeepSeek analysis provider"
  ```

### Task 2: Consistent backups, Syncthing quiescence, and retention

**Files:**
- Modify: `scripts/server_backup.py`
- Modify: `deploy/knowledge-manager-backup.service`
- Modify: `deploy/knowledge-manager-backup.timer`
- Create: `scripts/backup_retention.py`
- Modify: `tests/test_backup.py`
- Create: `tests/test_backup_retention.py`
- Modify: `scripts/check_backup_linux.py`
- Modify: `docs/server-operations.md`

**Interfaces:**
- Consumes: existing `backup_service(args)`, `create_backup(data_root, backup_root, config_root=None)`, and `verify_backup(archive)`.
- Produces: `backup_service` accepts `--companion-service syncthing@knowledge-manager.service`; `backup_retention.select_retained(paths, now)` returns the exact paths retained and never deletes an unmarked archive.

- [ ] **Step 1: Write failing retention and writer-state tests.**

  Create timestamped valid archive fixtures plus `.partial`, missing-checksum, and unrelated files. Assert selection keeps 24 hourly, 30 daily, 12 weekly, every upgrade-marked archive, and at least the newest verified copy. Extend the existing fake-systemd tests with a Syncthing companion that is active, inactive, and missing; assert stop order is callback, Syncthing, application and restart order restores only services that were active.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

  Run `python -m pytest tests/test_backup.py tests/test_backup_retention.py -q`. Expected result: retention and Syncthing companion assertions fail before implementation.

- [ ] **Step 3: Implement retention and service integration.**

  Add strict archive-name and checksum parsing to `scripts/backup_retention.py`. Make the selection function pure and deterministic by accepting `now`; pruning code must refuse paths outside the managed backup root and refuse to remove files without a verified manifest. Add `syncthing@knowledge-manager.service` as a required companion in the backup unit and change the timer to run hourly at minute zero. Preserve the existing signal-safe `finally` recovery path.

- [ ] **Step 4: Run Linux backup cancellation and restore tests.**

  Run `python -m pytest tests/test_backup.py tests/test_backup_retention.py -q` and `python scripts/check_backup_linux.py`. Expected result: all tests pass, cancelled archives leave no published or partial file, and the fixture restores its original service state.

- [ ] **Step 5: Commit the backup slice.**

  ```bash
  git add scripts/server_backup.py scripts/backup_retention.py deploy/knowledge-manager-backup.service deploy/knowledge-manager-backup.timer tests/test_backup.py tests/test_backup_retention.py scripts/check_backup_linux.py docs/server-operations.md
  git commit -m "feat: quiesce sync writer and retain verified backups"
  ```

### Task 3: Tailscale-protected Syncthing Vault synchronization

**Files:**
- Create: `deploy/syncthing@knowledge-manager.service`
- Create: `scripts/install_syncthing_ubuntu.sh`
- Create: `scripts/check_syncthing.py`
- Create: `tests/test_syncthing_config.py`
- Modify: `scripts/install_ubuntu.sh`
- Modify: `docs/server-operations.md`
- Create: `docs/obsidian-sync.md`

**Interfaces:**
- Consumes: `/srv/knowledge-manager/vault`, the `knowledge-manager` service account, Tailscale IPv4 discovery, and the backup companion unit from Task 2.
- Produces: a repeatable install/check flow that writes only local Syncthing configuration, a tested `.stignore`, and a status report distinguishing Tailscale reachability, Syncthing health, folder state, conflicts, and last sync time.

- [ ] **Step 1: Write failing configuration tests.**

  Test that generated folder configuration contains only the Vault path, Send & Receive type, 30-day staggered versioning, and explicit Windows/Android device IDs supplied at install time. Test that it ignores workspace state/cache files while retaining Markdown, attachments, and common Obsidian settings. Test that database, `server.env`, model cache, backup archive, and private originals outside the Vault cannot be selected as shared paths.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

  Run `python -m pytest tests/test_syncthing_config.py -q`. Expected result: the installer/check module and ignore-rule assertions are missing.

- [ ] **Step 3: Implement the service and configuration generator.**

  Add a systemd template running Syncthing as `knowledge-manager`, bound to loopback/Tailscale management addresses, without automatic public discovery or relay exposure. The installer must refuse to proceed if `tailscale ip -4` is unavailable, if the Vault is not a real directory, or if the service account is missing. Generate `.stignore` atomically and keep API/device IDs in root-readable local configuration only.

  Add `scripts/check_syncthing.py` to query the local Syncthing REST endpoint using a local API key, verify the shared folder path and device state, list conflict files, and return a nonzero status for a stale sync, unexpected shared folder, public bind, or missing versioning. Do not print the API key or device IDs in diagnostics.

- [ ] **Step 4: Run configuration tests and the dry-run installer.**

  Run `python -m pytest tests/test_syncthing_config.py -q` and `python scripts/install_syncthing_ubuntu.sh --check-only --vault /srv/knowledge-manager/vault`. Expected result: tests pass; check-only performs no service, firewall, or port change and reports missing external Tailscale/Syncthing prerequisites clearly.

- [ ] **Step 5: Document client pairing and commit.**

  In `docs/obsidian-sync.md`, document Windows and Android installation, Tailscale login, Syncthing device-ID exchange, Obsidian folder selection, conflict recovery, and the rule that the server Vault and verified backups are authoritative. Commit:

  ```bash
  git add deploy/syncthing@knowledge-manager.service scripts/install_syncthing_ubuntu.sh scripts/check_syncthing.py tests/test_syncthing_config.py scripts/install_ubuntu.sh docs/server-operations.md docs/obsidian-sync.md
  git commit -m "feat: add private Syncthing Obsidian synchronization"
  ```

### Task 4: Cloudflare Tunnel and hardened WeChat public callback

**Files:**
- Create: `deploy/cloudflared-knowledge-manager.service`
- Create: `scripts/install_cloudflared_tunnel.sh`
- Create: `scripts/check_public_entry.py`
- Create: `tests/test_public_entry.py`
- Create: `tests/fixtures/public-entry.yaml`
- Modify: `deploy/server.env.example`
- Modify: `vendor/knowledge-inbox/backend/wechat/crypto.py`
- Modify: `vendor/knowledge-inbox/backend/wechat/callback.py`
- Modify: `vendor/knowledge-inbox/backend/wechat/runtime.py`
- Modify: `tests/test_wechat_callback.py`
- Modify: `tests/test_wechat_worker.py`
- Modify: `docs/wechat-interface.md`
- Modify: `docs/server-operations.md`

**Interfaces:**
- Consumes: existing `create_public_app()`, `CallbackSettings`, `WeChatStore`, `WeChatWorker`, and local `127.0.0.1:8766` callback service.
- Produces: `cloudflared-knowledge-manager.service` and a check command proving only `/wechat/callback` is routed; callback remains the only public FastAPI route and returns durable/quick acknowledgement.

- [ ] **Step 1: Write failing public-entry and replay tests.**

  Assert generated Cloudflare config has one ingress rule for `/wechat/callback` and a final 404 rule, no management upstream, no public port bind, and no Access login requirement. Extend callback tests with stale timestamps, duplicate encrypted notifications, invalid receiver IDs, unknown senders, and oversized bodies. Keep existing tests for empty pages with `has_more=1`, cursor rollback, and reply quota.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

  Run `python -m pytest tests/test_wechat_callback.py tests/test_wechat_worker.py tests/test_public_entry.py -q`. Expected result: new replay/time-window and static tunnel checks fail before implementation.

- [ ] **Step 3: Implement the tunnel installer and callback hardening.**

  Make the installer accept a local Cloudflare tunnel token or credentials file path, require `WECHAT_PUBLIC_HOSTNAME`, create a root-owned 0600 local config, and install an outbound-only systemd service. Refuse empty hostnames, wildcard routing, management upstreams, and credentials embedded in Git-tracked files.

  Enforce callback timestamp window and authenticated receiver before persisting a notice; retain durable idempotency keyed by account and notification identity. Preserve fast acknowledgement, cursor transactionality, sender allowlist, media limits, reply quota, and redacted logs. Do not expose Swagger, management API, or Vault routes from `create_public_app()`.

- [ ] **Step 4: Run offline protocol tests and static entry checks.**

  Run `python -m pytest tests/test_wechat_callback.py tests/test_wechat_worker.py tests/test_public_entry.py -q` and `python scripts/check_public_entry.py --config tests/fixtures/public-entry.yaml`. Expected result: callback protocol, replay, route isolation, and configuration tests pass without contacting WeChat or Cloudflare.

- [ ] **Step 5: Commit the public-entry slice.**

  ```bash
  git add deploy/cloudflared-knowledge-manager.service scripts/install_cloudflared_tunnel.sh scripts/check_public_entry.py tests/test_public_entry.py deploy/server.env.example vendor/knowledge-inbox/backend/wechat tests/test_wechat_callback.py tests/test_wechat_worker.py docs/wechat-interface.md docs/server-operations.md
  git commit -m "feat: harden the public WeChat callback entry"
  ```

### Task 5: Live DeepSeek acceptance and provider-neutral evidence checker

**Files:**
- Create: `scripts/check_deepseek_ingestion.py`
- Modify: `scripts/check_local_ai_ingestion.py` (share provider-neutral checker functions or delegate)
- Create: `tests/test_deepseek_ingestion.py`
- Modify: `docs/validation-2026-09-19.md`
- Modify: `docs/ubuntu-deployment.md`

**Interfaces:**
- Consumes: running `/api/health`, `/api/upload`, job status endpoints, `OPENAI_API_KEY`, and the Task 1 evidence metadata.
- Produces: a redacted JSON acceptance report with model/provider, chunk coverage, evidence checks, elapsed time, HTTP error classes, peak RSS, and swap delta; exit code 0 only when all forced facts and all chunks pass.

- [ ] **Step 1: Write failing checker tests.**

  Use a synthetic 9,000+ character document with unique facts at the beginning, middle, and end. Assert the checker rejects missing chunks, discontinuous offsets, wrong quotes, mismatched model/provider metadata, missing Obsidian evidence, and a note that claims AI while `analysis_mode` is `rules`.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

  Run `python -m pytest tests/test_deepseek_ingestion.py tests/test_check_local_ai_ingestion.py -q`. Expected result: provider-neutral command and stricter metadata checks are absent.

- [ ] **Step 3: Implement the real acceptance command.**

  Add an explicit `--url`, `--timeout`, and `--output` interface. Refuse to run unless `AI_ENABLED=true`, `OPENAI_MODEL` and the base URL are set, and `OPENAI_API_KEY` is present in the process environment. Submit one synthetic item through the real queue, poll until terminal, read the saved note and SQLite row, and verify all chunk/evidence invariants. Redact paths, API keys, request bodies, and note content from the report.

- [ ] **Step 4: Run offline tests, then one live acceptance with the user’s key.**

  Run the focused tests first. On Ubuntu, run the live command only after the DeepSeek account is configured locally; record the actual model ID, latency, token usage, memory and swap change. Keep AI disabled if any forced fact, citation, retry, or resource criterion fails.

- [ ] **Step 5: Commit the acceptance slice.**

  ```bash
  git add scripts/check_deepseek_ingestion.py scripts/check_local_ai_ingestion.py tests/test_deepseek_ingestion.py docs/validation-2026-09-19.md docs/ubuntu-deployment.md
  git commit -m "test: verify DeepSeek grounded ingestion"
  ```

### Task 6: Ubuntu deployment, real device pairing, and end-to-end acceptance

**Files:**
- Modify: `scripts/install_ubuntu.sh`
- Modify: `scripts/activate_release.py`
- Modify: `deploy/knowledge-manager.service`
- Modify: `deploy/knowledge-manager-wechat.service`
- Modify: `deploy/knowledge-manager-backup.service`
- Modify: `deploy/knowledge-manager-backup.timer`
- Modify: `docs/ubuntu-deployment.md`
- Modify: `docs/server-operations.md`
- Modify: `docs/validation-2026-09-19.md`

**Interfaces:**
- Consumes: Tasks 1–5 release files, Ubuntu SSH management path, the existing backup exporter, the user’s DeepSeek API key, Cloudflare hostname/token, WeChat credentials, Windows client, and Android client.
- Produces: an activated release whose service graph is explicit, health output distinguishes configured from accepted features, and a timestamped evidence report for sync, real WeChat, AI, restart, and restore.

- [ ] **Step 1: Add deployment tests for service graph and fail-closed configuration.**

  Extend release activation tests to require the Syncthing companion in backup quiescence, keep Cloudflare and WeChat disabled when their local credentials are incomplete, start the main application before the callback, and leave local LLM disabled. Assert an invalid DeepSeek or tunnel configuration aborts before any service is enabled.

- [ ] **Step 2: Run deployment tests and local full verification.**

  Run `python -m pytest -q` and `python scripts/verify.py`. Expected result: the new service graph assertions fail until the unit files and activation logic are implemented; after implementation all existing and new tests pass.

- [ ] **Step 3: Deploy the release through the existing verified activation path.**

  Create the pre-upgrade verified archive, install the release into a new `/opt/knowledge-manager/releases/<version>`, install systemd units, reload systemd, and keep AI/WeChat/Cloudflare/Syncthing disabled until their individual prerequisites are present. Verify `/api/health`, storage paths, ownership, and service states before enabling each component.

- [ ] **Step 4: Pair Windows and Android and perform the sync matrix.**

  On Windows, install Syncthing and Obsidian, exchange the device ID through Tailscale, accept only the Vault folder, and verify the ignore rules. On Android, install Tailscale, Syncthing-Fork, and Obsidian, exchange the device ID, select the local Vault folder, and perform the server→client, Windows→server, Android→server, offline conflict, delete/recover, and restart/reconnect checks. Record file hashes and conflict paths without storing note contents in Git.

- [ ] **Step 5: Configure and verify DeepSeek and the real WeChat path.**

  Store the DeepSeek key, Cloudflare tunnel token/hostname, and official WeChat credentials only in Ubuntu local configuration. Run Task 5 live acceptance, then send real allowed-user messages for text, URL, PDF, Word, Markdown, Excel, image, voice, and video. Verify original bytes, source type, task/result rows, DeepSeek metadata, Obsidian note, duplicate callback behavior, retry, quota, and restart continuation.

- [ ] **Step 6: Run restart, backup, and restore drills.**

  Restart the Ubuntu VM and Windows host services, wait for Tailscale/Syncthing/Cloudflare/application readiness, verify health and device convergence, create and copy a full archive, verify its SHA-256 on both disks, restore to a new directory, run SQLite `integrity_check`, compare item/task counts and attachment hashes, and document any external/offline-backup limitation.

- [ ] **Step 7: Commit deployment evidence.**

  ```bash
  git add scripts/install_ubuntu.sh scripts/activate_release.py deploy docs/ubuntu-deployment.md docs/server-operations.md docs/validation-2026-09-19.md
  git commit -m "docs: record remote service acceptance"
  ```

### Task 7: Whole-branch review and GitHub delivery

**Files:**
- Modify: `README.md`
- Modify: `docs/validation-2026-09-19.md`
- Modify: `docs/ubuntu-deployment.md`

**Interfaces:**
- Consumes: all task commits, local/remote acceptance reports, and the existing `validation/knowledge-inbox` pull request.
- Produces: a clean, reproducible branch whose source tree, CI result, deployment release, and evidence report are traceable without credentials or private knowledge.

- [ ] **Step 1: Run the complete local verification suite.**

  Run `python -m pytest -q`, `python scripts/verify.py`, `git diff --check`, and the relevant Ubuntu fixture checks. Expected result: all tests pass, no formatting errors, no local LLM or cloud credentials are enabled by defaults, and no temporary acceptance output is tracked.

- [ ] **Step 2: Scan for secret and knowledge leakage.**

  Run repository scans for `OPENAI_API_KEY=`, `WECHAT_SECRET=`, `TOKEN=`, private-key headers, `/srv/knowledge-manager/vault` content, media binaries, and runtime backup files. The only matches allowed are example variable names, tests using synthetic values, and documentation explaining local configuration.

- [ ] **Step 3: Compare local and GitHub trees without force push.**

  Create the remote tree from the current remote head, create a commit with the local tree, update `validation/knowledge-inbox` with `force=false`, and verify the returned tree SHA equals `git show -s --format=%T HEAD`. Do not force-update the branch.

- [ ] **Step 4: Verify CI and update the existing PR.**

  Wait for Windows and Ubuntu GitHub Actions jobs to finish successfully. Update PR #1 title/body if the final scope changed, attach the existing PR to this task, and include links to the plan, design, validation report, and deployment limitations.

- [ ] **Step 5: Final commit.**

  ```bash
  git add README.md docs/validation-2026-09-19.md docs/ubuntu-deployment.md
  git commit -m "docs: finalize remote knowledge manager delivery"
  ```

## Execution Notes

The external configuration gates are concrete prerequisites: the user must provide a DeepSeek API key, a Cloudflare-managed hostname and tunnel credential, a configured official WeChat customer-service account, and one interactive pairing session on Android. Until each gate has evidence, its feature remains disabled and the final report says exactly which acceptance is pending.

The first implementation slice should be Task 1, because both the ingestion acceptance and the final Obsidian note contract depend on the provider-neutral AI metadata. Tasks 2–4 can then proceed in parallel at the code level but must be deployed sequentially on the Ubuntu host. Task 6 is the only step allowed to enable production services. Task 7 is the completion gate.
