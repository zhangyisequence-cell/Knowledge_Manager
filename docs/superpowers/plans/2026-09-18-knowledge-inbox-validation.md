# Knowledge Inbox Validation Plan

**Goal:** 验证 Knowledge Inbox 是否适合作为微信收集、本地 Obsidian 知识库的处理底座，并把所有源码与验证材料交付至用户仓库。

**Architecture:** 固定上游提交，保留完整源码及许可证。Python 独立环境运行服务，以合成样本验证 API、解析、任务状态、数据库及 Markdown 输出。测试知识库与真实资料隔离。

**Tech Stack:** Python 3.12, FastAPI, pytest, httpx, python-docx, reportlab.

## Constraints

- Target repository: https://github.com/zhangyisequence-cell/Knowledge_Manager
- Intended host: 192.168.3.189; OS and remote access not yet verified.
- No API keys, credentials, real knowledge vault, downloaded models or runtime databases in Git.
- No unauthenticated public exposure. Initial service binds only 127.0.0.1.
- No claim of real AI quality based on mocks or fallback summaries.
- Preserve original uploaded files; do not automatically delete videos.

## Tasks

- [x] Import pinned upstream source with attribution.
- [x] Install isolated runtime and run upstream pytest and Ruff baseline.
- [x] Add acceptance tests for real API ingestion of Chinese text, TXT, DOCX and PDF; check task completion and generated Markdown.
- [x] Verify document tables and attachment portability; record reproducible failures before any minimal repair.
- [x] Verify webpage extraction with a local HTTP fixture and a public webpage separately.
- [x] Verify AI-disabled behavior, unsupported format failure and API boundary.
- [x] Assess media/OCR prerequisites; report untested real AI/video capabilities explicitly.
- [x] Provide local setup/run/verification scripts and deployment guidance.
- [x] Re-run tests, inspect Git contents for secrets, and commit all project source locally.
- [ ] Push to the user's repository after GitHub write authentication becomes available (device-login endpoint timed out).

## Verification commands

```powershell
.venv/Scripts/python.exe scripts/verify.py
```

Docker execution and remote deployment are separate checks, not implied by passing Python tests.
