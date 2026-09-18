# Knowledge Manager

微信收集 → 本地解析整理 → Obsidian 知识库的试点工程。

当前阶段：验证 Knowledge Inbox 作为资料处理底座。**微信聊天入口、外网服务和真实 AI 摘要尚未验收，不能当作已完成的整套知识库。**

目标仓库：https://github.com/zhangyisequence-cell/Knowledge_Manager

## 已有内容

- 完整上游源码快照，固定版本及许可证见 [UPSTREAM.md](UPSTREAM.md)。
- 中文文本、TXT/Markdown、Word 正文和表格、文字型 PDF、网页入库验证。
- 三处修补：Word 表格提取；空白 PDF 不再假成功；原始附件复制进 Vault，使用相对链接。
- 仅本机访问的试用服务、独立试用 Vault、复现脚本、依赖版本锁定。
- Windows / Linux CI 配置。云端 CI 只有推送后实际运行，配置存在不代表已经通过。

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

需要系统提供 Python 3.12+ 及 venv。媒体处理还需要 FFmpeg；扫描 PDF/图片 OCR 需要 Tesseract 及 `chi_sim`、`eng` 语言包。Debian/Ubuntu 对应软件包为 `ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng`。

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

## 启用真实 AI（尚未验收）

默认 AI 关闭。此时“摘要”只是规则提取，分类为“待分类”，不是智能总结。

在当前终端设置 `KNOWLEDGE_OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_API_KEY` 后启动：

```powershell
.venv/Scripts/python.exe scripts/run_local.py --enable-ai
```

使用云端 AI 会向配置的供应商提交正文/转写内容。不要把密钥放入代码、截图或 Git。模型必须支持项目使用的 Chat Completions JSON 输出；视频视觉输入并非所有兼容服务均支持。

## 旧笔记本与外网访问

预定目标：Windows，`192.168.3.189`，SSH 用户 `Administrator`。本次只在当前工作电脑验证，尚未安装到目标机器。若端口仍未连通，可在目标电脑运行只读检查脚本 `scripts/diagnose-host.ps1`，核对 sshd 服务、监听端口和防火墙规则。

上游接口缺少完整身份认证，本项目入口因此固定监听 `127.0.0.1`。局域网和外网访问的下一步应是受限的私人组网入口（例如 Tailscale Serve 配合设备访问规则），或具备身份认证的反向代理。不要直接做公网端口映射。

Obsidian 是读取本地文件夹的客户端。手机/外出电脑使用完整 Obsidian，需要另外选择文件同步方案；能远程打开收集页面不代表 Vault 已自动同步。

## 数据与备份

- `runtime/vault/`：试用知识库，生成笔记和随库附件。
- `runtime/data/`：上传原件、处理任务数据库、缓存。
- `runtime/model-cache/`：语音模型缓存。
- `config.local.yaml`：可选本机配置；不提交 Git。

上述目录与密钥文件已排除版本控制。备份 Vault 时会带上附件；需要恢复处理任务时还应停服后备份 `runtime/data`。Git 仓库保存代码，不代替知识库备份。

## 后续顺序

代码已交付至本仓库的 `validation/knowledge-inbox` 分支，见 [试点草稿 PR #1](https://github.com/zhangyisequence-cell/Knowledge_Manager/pull/1)。

1. 打通目标机器远程登录，将已验证的试点部署到旧笔记本。
2. 用真实公众号文章、复杂文档、中文视频和选定 AI 模型验收。
3. 确定微信提交入口，再接入资料处理队列。
4. 配置私人外网访问、Obsidian 同步、开机自启和备份。
