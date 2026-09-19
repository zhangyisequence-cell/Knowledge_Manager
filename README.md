# Knowledge Manager

微信收发 → CentOS 服务器解析整理 → 本地 Obsidian 知识库。

当前阶段：已完成 Knowledge Inbox 的 DeepSeek、备份、Tailscale/Syncthing 和 Cloudflare/微信代码切片及离线验证；**真实 DeepSeek key、微信账号、Cloudflare hostname、Windows/安卓配对和 CentOS 端到端验收仍未完成，不能当作生产系统已上线。**

目标仓库：https://github.com/zhangyisequence-cell/Knowledge_Manager

## 已有内容

- 完整上游源码快照，固定版本及许可证见 [UPSTREAM.md](UPSTREAM.md)。
- 中文文本、TXT/Markdown、Word 正文和表格、文字型 PDF、网页和 Excel 入库验证。
- Excel 支持 XLSX/XLSM/XLS：保留工作表与行列位置、文本、日期、数值、布尔值；现代格式记录普通/数组公式与缓存标记，原件随库保留。
- 三处修补：Word 表格提取；空白 PDF 不再假成功；原始附件复制进 Vault，使用相对链接。
- 仅本机访问的试用服务、独立试用 Vault、复现脚本、依赖版本锁定。
- DeepSeek OpenAI 兼容提供方：只发送正文、表格文本、OCR 文本和音视频转写文本；原始二进制留在 CentOS。
- CentOS Vault 只通过 Tailscale 保护的 Syncthing 同步到 Windows 与安卓；数据库、密钥、模型缓存和备份不共享。
- Cloudflare Tunnel 只转发 `/wechat/callback`，其他公网路径返回 404；回调具有时间窗口、白名单和持久幂等。
- Windows / Linux CI：既有试点已在两种环境通过，后续提交的结果以 PR 检查为准。

详细结果与局限见 [验证报告](docs/validation-report.md)。

## Windows 上启动

需要 Python 3.12+（本次实测 Python 3.12）。在仓库根目录执行：

```powershell
powershell -File scripts/setup.ps1 -Python python
.venv/Scripts/python.exe scripts/run_local.py
```

若 `python` 是 Microsoft Store 占位程序，把 `-Python` 改成实际 Python 可执行文件路径。

打开 http://127.0.0.1:8787 。在 Obsidian 中选择“打开本地仓库”，选本项目的 `runtime/vault` 文件夹。

## Linux 上启动

```sh
sh scripts/setup.sh
.venv/bin/python scripts/run_local.py
```

需要系统提供 Python 3.12+ 及 venv。媒体处理还需要 FFmpeg；扫描 PDF/图片 OCR 需要 Tesseract 及 `chi_sim`、`eng` 语言包。CentOS 安装入口会启用 EPEL/CRB，并安装 `ffmpeg-free`、`antiword`、`tesseract` 及对应语言包。

`requirements.lock` 固定本次解析的依赖版本；尚未在目标旧笔记本上验证安装。Python 包安装不包含语音模型或 Tesseract 系统程序。

## 复现验证

```powershell
.venv/Scripts/python.exe scripts/verify.py
# 服务启动后，在另一终端运行：
.venv/Scripts/python.exe scripts/smoke.py --web-url https://www.python.org/about/
```

Linux 使用 `.venv/bin/python`。回归测试强制关闭模型下载，并使用合成资料和临时目录。它们不验证真实大模型效果。

真实媒体验证独立运行，需要语音模型下载成功：

```powershell
powershell -File scripts/make_video_sample.ps1
.venv/Scripts/python.exe scripts/media_probe.py runtime/samples/validation.mp4
```

生成器使用 Windows 自带语音合成和 FFmpeg，内容仅为虚构验证语句，不涉及个人录音。模型默认 `small`；首次下载需要访问 Hugging Face。测试输出在 `runtime/`，不会提交 Git。

## 启用真实 AI（需先完成验收）

默认 AI 关闭。此时“摘要”只是规则提取，分类为“待分类”，不是智能总结。

在 CentOS 本地配置 `KNOWLEDGE_OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-chat` 和 `OPENAI_API_KEY` 后启动：

```powershell
.venv/Scripts/python.exe scripts/run_local.py --enable-ai
```

使用 DeepSeek 会提交正文、表格文本、OCR 文本和音视频转写内容；原始 PDF、Word、Excel、图片、音频、视频不会上传。不要把密钥放入代码、截图或 Git。启动前运行 `scripts/check_deepseek_ingestion.py --output /var/tmp/deepseek-acceptance.json`，只有连续分段、逐字证据、SQLite 与 Obsidian 校验全部通过才保持 AI 开启。

## 旧笔记本与外网访问

最终部署目标是旧笔记本上的 **CentOS Stream/RHEL-compatible 9 或 10**。服务器通过 Tailscale 私网管理；部署脚本会 fail-closed：未提供 DeepSeek、微信或 Cloudflare 本地凭据时不启用对应服务。**真实服务器、手机配对和公网回调仍需按验证记录完成，不能把代码测试视为部署完成。**

部署步骤和 CentOS 安装脚本见 [CentOS 部署记录](docs/centos-deployment.md)。微信入口按用户选择采用公众号/微信客服方向，优先核实可接收文件的微信客服；尚未配置真实账号。

管理面和主应用仍固定监听回环地址；Tailscale 只用于 Syncthing/SSH 私网管理，Cloudflare 只承载微信回调。不要直接做公网端口映射。

Obsidian 通过 Syncthing 同步服务器 Vault；配对和冲突处理步骤见 [Obsidian 同步](docs/obsidian-sync.md)。服务器 Vault 与已验证备份是恢复依据。

## 数据与备份

- `runtime/vault/`：试用知识库，生成笔记和随库附件。
- `runtime/data/`：上传原件、处理任务数据库、缓存。
- `runtime/model-cache/`：语音模型缓存。
- `config.local.yaml`：可选本机配置；不提交 Git。

上述目录与密钥文件已排除版本控制。备份 Vault 时会带上附件；需要恢复处理任务时还应停服后备份 `runtime/data`。Git 仓库保存代码，不代替知识库备份。

## 后续顺序

代码已交付至本仓库的 `validation/knowledge-inbox` 分支，见 [试点草稿 PR #1](https://github.com/zhangyisequence-cell/Knowledge_Manager/pull/1)。

1. 在 CentOS 本地配置 DeepSeek、Cloudflare 和企业微信客服凭据。
2. 完成真实 DeepSeek 长文验收和微信文字/链接/文件/音视频端到端验收。
3. 配对 Windows 与安卓 Syncthing，完成离线冲突、重启、备份和恢复演练。
4. 将验证证据补入 [CentOS 部署记录](docs/centos-deployment.md) 后再启用生产服务。

## Excel 解析边界

每个工作表最多 50,000 行、256 列，整个工作簿累计扫描最多 250,000 个单元格，现代文件解压总大小上限 64 MiB；超限会明确失败，不截断后冒充完整入库。空白、损坏的表格同样报告失败。

提取的是单元格内容，数字保留数值，不承诺 Excel 显示格式、图表、绘图和版式还原。公式不重新计算，不执行宏；无缓存标记“未缓存，未计算”，缓存可能过期，必须回原文件核对。XLS 只能读取已存储的计算值；XLSX/XLSM 另外记录公式表达式或数据表运算参数。
