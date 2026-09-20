# Server Persistence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session.

**Goal:** Ubuntu 服务的数据独立于程序升级保存，并提供可校验、可恢复的一致性备份。

**Architecture:** 应用使用 `/srv/knowledge-manager/{data,vault,model-cache}`；systemd 专用账户运行。备份先停止写入服务，归档 data/vault/config，散列校验后才发布结果，随后恢复此前运行的服务。恢复只能写新目录，验证全部原件和 SQLite 完整性，不能覆盖正在使用的知识库。

**Tech Stack:** Python 标准库 tarfile/hashlib/sqlite3、systemd、Ubuntu 24.04。

**Spec:** `docs/superpowers/specs/2026-09-18-ubuntu-knowledge-server-design.md`

## Global Constraints

- 真实资料和密钥不进 Git。
- 不通过删除原件或覆盖目标来简化备份恢复。
- 只有实际恢复演练及重启验证通过，才能宣称数据保存已验收。
- 同盘备份不能防硬盘损坏；第二介质配置仍需实际完成。

## Task 1: 归档和恢复

**Files:** `scripts/server_backup.py`, `tests/test_backup.py`。

**Interfaces:** `create_backup(data_root, backup_root, config_root=None) -> Path`（调用者须先停止写入）；`verify_backup(archive) -> dict`；`restore_backup(archive, destination) -> Path`。

- [ ] 写真实 SQLite 数据库和中文 Vault/附件样本的往返测试：恢复到新目录后逐字节一致，数据库 `PRAGMA integrity_check` 为 ok；模型缓存不入包。
- [ ] 写篡改内容、路径穿越、符号链接、已存在目标目录的拒绝测试，运行确认失败。
- [ ] 实现归档内部 manifest 逐文件 SHA-256/size 校验，临时归档验证完成再原子改名；恢复不覆盖目录，拒绝非普通文件和逃逸路径。
- [ ] CLI 使用 systemctl 停止服务并在 finally 中恢复此前运行状态；每日定时器调用 CLI。先保留所有备份，不在未确认存储容量和保留策略前自动删除历史。
- [ ] 运行本地完整回归、Ubuntu CI，随后在真实服务器恢复到独立目录，验证附件和 SQLite。

## Task 2: 服务器运行

**Files:** `scripts/run_server.py`, `scripts/install_ubuntu.sh`, `deploy/knowledge-manager.service`, `deploy/knowledge-manager-backup.service`, `deploy/knowledge-manager-backup.timer`, `deploy/server.env.example`。

- [ ] 核实已安装系统的版本、磁盘、sudo、网络和 Python。
- [ ] 安装 Python3.12、FFmpeg、中文 OCR 等依赖；程序部署到 `/opt/knowledge-manager/current`，服务以 knowledge-manager 用户运行，数据放 `/srv/knowledge-manager`。
- [ ] 保持 API 只监听 loopback，由受限入口转发；AI 按配置显式开启，不冒充已完成智能分析。
- [ ] 验证服务启动、真实上传及关机再启动后数据仍在。
- [ ] 提交所有脚本、配置及测试到用户 GitHub，并核对远端文件和 CI。
