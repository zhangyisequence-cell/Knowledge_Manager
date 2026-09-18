# Knowledge Inbox 来源

- Repository: https://github.com/lyc403223157-source/knowledge-inbox
- Commit: `0dafd4e3a7d31fd14a04d3dc9f077d88f26ca8f0`
- Imported: 2026-09-18
- Location: `vendor/knowledge-inbox/`
- License: Apache-2.0; original LICENSE and THIRD_PARTY_NOTICES.md retained.

源码以完整文件快照纳入本项目，不使用依赖外部仓库才能取到源码的子模块。
本项目新增脚本、验证和文档放在仓库根目录对应子目录。
上游修补记录在验证报告中；未经记录不得宣称快照与上游完全一致。

## 本项目修改过的上游文件（2026-09-18）

- `backend/adapters/local_file.py`：保留普通 Word 表格文本。
- `backend/adapters/pdf.py`：无有效正文时报告失败。
- `backend/storage/obsidian.py`：原始附件复制进 Vault，使用相对链接。

上述修改由本项目进行，非原作者发布版本；其余导入文件保留上游来源。
