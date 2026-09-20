# Excel Ingestion Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute this plan in the current session.

**Goal:** 将现代和传统 Excel 经现有上传队列解析到 Obsidian，同时保留原件。

**Architecture:** 独立 SpreadsheetAdapter 负责 XLSX/XLSM 与 XLS，复用现有任务、AI 和笔记保存；不在微信层解析。

**Tech Stack:** Python 3.12、openpyxl、xlrd、pytest、FastAPI TestClient。

**Spec:** `docs/superpowers/specs/2026-09-18-ubuntu-knowledge-server-design.md`

## Global Constraints

- 服务器最终是用户的 Ubuntu；当前测试不能替代远端验收。
- 不能执行宏或公式；缺少公式缓存必须标注；保留原文件。
- 不默默截断大表；空白/损坏表格明确失败。
- 所有代码和测试提交用户仓库，不上传真实知识资料或密钥。

## Task 1: Excel 上传到 Vault

**Files:** `vendor/knowledge-inbox/backend/adapters/spreadsheet.py`、`backend/adapters/registry.py`、`backend/api/routes.py`、`vendor/knowledge-inbox/pyproject.toml`、`requirements.lock`、`tests/test_acceptance.py`、`tests/fixtures/legacy.xls`。

**Interfaces:** `SpreadsheetAdapter.detect(Path) -> bool`；`fetch(Path, **kwargs) -> FetchedContent`；`source_type = spreadsheet`。工作表按原顺序导出，非空行以 Markdown 表格保存，第一列为原行号，其他列用 A/B/C 标识。

- [x] 新增 API 验收：含中文、两个工作表、布尔、日期、金额、公式无缓存的 XLSX 上传后核对实际 Markdown 与附件字节；XLS 用固定合成二进制样本核对工作表、金额和日期。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/test_acceptance.py -k excel -q`，确认不支持的扩展名导致失败。
- [x] 注册适配器，XLSX 用 openpyxl 分别读取公式与缓存，XLS 用 xlrd 读取类型化值；上下文退出关闭工作簿；统一转义 Markdown 管道符与换行。
- [x] 新增空白、损坏、大表拒绝测试，保证任务失败且没有成功笔记。
- [ ] 运行上述验收、完整 `scripts/verify.py`、`pip check`；更新格式清单和上游修改记录并提交 GitHub，核对 CI。

## 后续独立工作（不因本计划完成而标记总目标完成）

1. 按目标 Ubuntu 版本准备服务部署与一致性备份恢复。
2. 验证旧式 Word、中文媒体、OCR、长文 AI 与分类检索。
3. 根据实际微信账号配置连接器，验收双向收发及重复消息处理。
4. 在服务器完整演练入库、重启、备份恢复和 Obsidian 访问。

复核补充：独立审查的 4 类问题已逐一先复现再修复，共增加 5 项 Excel 回归；当前完整测试 35 + 28 = 63 项通过，Ruff 与 pip check 通过。远端 CI 仍需核对此次实际提交。
