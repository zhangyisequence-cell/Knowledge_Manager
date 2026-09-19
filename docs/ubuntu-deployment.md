# Ubuntu 部署记录

## 当前 DeepSeek 配置门槛

生产分析使用 DeepSeek OpenAI 兼容 API：`KNOWLEDGE_OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-chat`。API key 只写入 Ubuntu `/etc/knowledge-manager/server.env` 或同等 0600 本地配置，不能进入 Git、备份日志或验收输出。启用前运行 `scripts/check_deepseek_ingestion.py --output <本地证据文件>`，必须确认所有分段覆盖、证据偏移、SQLite 原文和 Obsidian `analysis_provider=deepseek` 均通过；失败时保持 `AI_ENABLED=false`。历史 Qwen/llama.cpp 结果只说明旧模型质量不足，不能作为当前生产 AI 通过依据。

最终平台由用户确认：原先提到 CentOS，实际允许使用现有 Hyper-V 的 Ubuntu。本文记录已观察到的事实，不把配置文件存在当作部署完成。

## 最新状态（2026-09-19 18:31）

当前应用 release 为 `/opt/knowledge-manager/releases/bfc579a`，18:17 升级单元成功。真实 HTTP 上传 WAV、MP4、AMR 的原件、Vault 附件、SQLite 转写、笔记转写与模型来源全部一致；严格转写准确性仍各为 4/5，“归档”均识别错误。笔记会明确提示“自动转写，未经人工复核”。新版本已实测将网页上传音视频标为 `local_file`，仅把微信实际下载的媒体标为 `wechat`。

第二物理盘 D: 的每小时备份已运行，首次复制与定时复用成功；从 D: 副本恢复并验证 67 个文件、SQLite 完整性及 23 条目/23 个成功任务。完整证据见 [验证记录](validation-2026-09-19.md)，操作方法见 [第二盘备份](second-disk-backup.md)。

Whisper 固定快照已离线校验并发布到服务账户的模型缓存。旧 CPU 的实际三格式转写耗时 152.63 秒、峰值约 1 GiB、无 swap。llama.cpp 固定源码已编译并通过运行兼容性检查；Qwen 1.5B 固定文件按大小和 SHA-256 校验后原子发布。环回推理服务健康检查及一条合成 JSON 请求通过，小请求耗时 16.108 秒。9,641 字四段验收在把实测超时调到 600 秒后完整处理，无截断、峰值约 323 MB、swap 无增长，但仅提取了开头事实，漏掉中段预算和尾部日期。应用已恢复 `ai_enabled=false`，LLM 停止且保持 disabled；该 1.5B 模型不作为正式智能总结使用。

18:26 完成部署后整机重启。DHCP 地址由 `172.27.65.231` 变为 `172.27.67.99`，固定 SSH 主机指纹仍通过；应用和备份 timer 自动恢复，AI/LLM/微信保持关闭。临时预览脚本自动发现新地址，健康接口和页面均返回 200。

下文保留各阶段的历史事实；较早的“待配置/尚未完成”状态以本节和最新验证记录为准。微信账号接入和远程 Obsidian 客户端使用仍待完成；本地 AI 需要换用质量合格的模型后重新验收。

## 目标和初始状态

- Windows 宿主机：Tailscale `100.64.186.105`，`Administrator`；已通过专用 Ed25519 密钥登录。
- 虚拟机：`Ubuntu`，ID `9f11fb39-9762-47ab-851d-a63f38e0f71b`；第二代，4 GiB RAM，当前 2 vCPU。
- 网络：Default Switch，MAC `00:15:5d:03:17:03`；安装前没有来宾 IP 或心跳。
- 安装镜像：宿主机现有 Ubuntu 24.04.4 live-server amd64 ISO。
- 启动故障：控制台显示镜像签名被拒绝；固件使用 `MicrosoftWindows` 模板。
- 在控制台确认未启动 OS 后停止虚拟机，仅只读挂载系统盘检查：`E:\Ubuntu\Ubuntu\Virtual Hard Disks\Ubuntu.vhdx` 为 RAW、无分区，虚拟容量约 127 GiB，文件占用 4 MiB。随后卸载只读挂载。这是允许在空盘安装的前置证据。

## 已执行的准备

1. `scripts/configure-hyperv-linux.ps1` 在虚拟机关闭时设置 Linux CA 安全启动模板，保留安全启动；保留系统盘，暂卸安装介质，先验证硬盘能否独立引导。结果为无法加载 OS。
2. `scripts/build_ubuntu_seed.py` 生成 NoCloud 公钥安装盘，用户名 `knowledgeadmin`，主机名 `knowledge-server`。禁用 SSH 密码登录，只安装管理公钥，私钥仍在管理电脑；专用管理员通过 sudo 管理部署。
3. 校验本地与远端安装配置 ISO 的 SHA-256 一致；重新挂载 Ubuntu 安装镜像及新配置盘，原来的 `cidata-seed-v2.iso` 不使用。
4. 启动镜像后，实际控制台进入 Subiquity 自动安装配置，读取到了网络、文件系统、身份和 SSH 配置。确认对象是上述 RAW 空盘后，继续自动安装。配置要求安装结束关机。

安装器完成最后的安全更新后已自动关机。2026-09-19 约 02:13，虚拟机在全部卸载安装 ISO 和配置盘后从系统盘成功引导 Ubuntu 24.04.4。正式系统最初通过 Default Switch DHCP 获得 `172.27.70.12`；短暂正常关机调整内存后重新启动，地址变为 `172.27.65.231`，证明该地址不能写成固定服务地址。重启后再次验证了专用 `knowledgeadmin` 公钥 SSH 登录和 `sudo`，固定 SSH 主机密钥与受信任 Hyper-V 控制台显示的指纹相同。时区已设为 `Asia/Shanghai`，NTP 已同步。根盘容量约 124 GiB，剩余约 112 GiB。

硬件实查：Intel i5-3320M，宿主机约 12GB 内存；VM 当前固定 4 GiB 内存、2 vCPU。根盘约 124 GiB、剩余约 112 GiB。本地大模型必须按该资源实测，不能直接使用默认 7B 配置。宿主机交流电休眠当前为 `never`，这是观察到的既有状态，并非本次部署修改。服务器上的语音模型元数据三个文件已经校验，主模型正在服务器直接断点续传，尚未完成或发布；不能把管理电脑上的完整模型误写成服务器已就绪。

部署源码本地提交 `2a76c089e3c1c773b291f129911d4ddd000b5f93`、远端提交 `5cafe99bb70706a7e14fe37a16a07a263e2ff45a` 的 Git tree 均为 `36762501b1edee5e33d006bf6d08f256eb4da330`；服务器首次成功激活的应用 release 为 `/opt/knowledge-manager/releases/2a76c08`。该版本于 2026-09-19 03:45:17 安装成功，`/api/health` 返回 `status=ok`、`storage_configured=true`、AI 关闭；`knowledge-manager.service` 与备份 timer 均为 enabled/active，微信 service 为 disabled/inactive。后续当前版本已更新为下述 `879926c`。

七类真实入库脚本已在服务器全部通过：中文文本、Markdown、TXT、含表格 DOCX、多工作表且保留公式标记的 XLSX、英文 PDF 和旧 DOC；原文、原件及附件哈希均已核验。公开的 SQLite `about.html` 也已真实抓取入库，已核对笔记中保留的来源 URL 与正文，整份笔记为 5,193 字节。首次正式备份 `knowledge-20260918T194804-800684b8.tar.gz` 已创建并验证 23 个文件，独立恢复到 `/var/tmp/km-restore-20260919-0348` 后 SQLite `integrity_check` 为 `ok`，包含 7 个条目和 7 个成功任务。

新 release 激活后的正式备份 `knowledge-20260918T200601-bda375a6.tar.gz` 于 04:06:02 成功完成并验证 67 个文件；恢复到 `/var/tmp/km-restore-879926c-0406` 返回退出码 0，独立 SQLite `integrity_check` 为 `ok`，包含 23 个条目和 23 个成功任务。恢复后主应用和备份 timer active、微信 inactive，健康接口为 `status=ok`、`storage_configured=true`、AI 关闭。备份重启应用后立即执行的一次 curl 曾因服务尚未完成启动而连接被拒绝，待启动完成后健康恢复正常；这不是持续故障。第二物理备份介质仍未配置。

中文混合 PDF 的真实 OCR 已正确识别扫描页中的金额、李明、日期和“归档”，空白页也正确处理；但同一 PDF 的中文可复制文字层因 Type0 `/UniGB-UTF16-H` 且没有 `ToUnicode`，通过 pypdf 提取时出现乱码。代码已改用 PyMuPDF。修复源码本地提交为 `879926ca190444dd49d91eb3a1b20342e132ef2e`、远端提交为 `6aa0cb1f9882c37dfb6630aa23d9a0465dc48f5e`，共同 tree 为 `f654051995799804d0c7984af5dc9f58adb60190`；新归档 SHA-256 为 `747327e5eab3bdd971a6edc2d10bdb391031d36e956353b878624ff6b881af50`，CI run `35388693783` 的 Windows 和 Ubuntu job 均已独立查询为成功。release `/opt/knowledge-manager/releases/879926c` 已于 03:59:49 正式激活，安装更新单元结果为 success/inactive，`current` 指向该 release；激活前备份为 `knowledge-20260918T195944-2962f908.tar.gz`。

新 release 的七类真实入库已再次全部通过，证据为 `/var/tmp/km-acceptance-879926c/server-ingestion-results.json`。中文混合 PDF 的独立实际 Tesseract 检查 7 项均为 true、退出码为 0，证据为 `/var/tmp/km-acceptance-879926c/ocr/chinese-ocr-result.json`。随后经 HTTP 上传的任务 `e29b8170bcc8466aaf6289ccae7fdae6` 成功；`verify_artifacts` 核对可复制中文、12800、李明、“知识归档”、空白页标签、规则模式和标签、服务端原件以及 Vault 附件 SHA 均为 true，日期在去空格后匹配。产物包含两个附件：原 PDF 和扫描页图片。完整证据为 `/var/tmp/km-acceptance-879926c/ocr/server-ocr-ingestion.json`。

模型下载器已查明镜像 HTTP 403 的原因：镜像拒绝 Python 默认 User-Agent，项目专用 User-Agent 可用。下载器测试现为 13 项并已通过，新增覆盖完整 `.part` 无网络发布及损坏的完整 `.part` 重新下载；配套 Qwen 下载测试 4 项，合计 17 项。完整统一测试总数仍为 317。`scripts/check_server_ingestion.py` 已用于上述七类正式服务器入库验收。

本地 AI 部署脚本已提交（本地 `22078ba`，远端 `bc923550b872e27b34f91b01dd1af4fb7663d98d`，对应 CI 成功），固定使用 llama.cpp CPU 源码和 Qwen 1.5B。旧 CPU 上的临时单元 `knowledge-manager-llm-build` 正在通过 apt 安装编译依赖。一次性单元 `knowledge-manager-llm-model-download` 已创建为串行队列，只有 Whisper 下载单元结束且完整校验通过后才开始固定 Qwen 下载；当前尚未传输 Qwen 数据，也没有实际 AI 推理结果。03:59 时 Whisper 主模型临时文件为 287,186,944 / 483,546,902 字节，仍未下载完成。目标 CPU 上 CTranslate2 报告支持 `float32`、`int16`、`int8` 和 `int8_float32`；这只证明 CPU 后端兼容能力查询成功，不等同于真实转写验收。

只读网络调查看到宿主 Intel 6205 Wi-Fi 当前仅协商约 13–14 Mbps、信号约 60%，到本地网关延迟抖动明显；来宾 Hyper-V 虚拟链路报告 10 Gbps，未见错误、丢包或队列限速。这些证据指向宿主无线链路可能是主要限制因素，但不作唯一因果断言。已询问是否可接宿主千兆有线网，尚未得到答复，未修改网络配置。

临时预览复用既有 SSH，仅在管理电脑 `127.0.0.1:18787` 绑定并转发到来宾 `127.0.0.1:8787`；实测健康接口 `status=ok`、`storage_configured=true`、AI 关闭，页面返回 HTTP 200、27,287 字节。没有新增持久端口或防火墙规则，这与仍待授权的持久 SSH `2222` 入口不同。该临时预览不代表手机或外网客户端访问已完成，也不代表 Obsidian 同步已完成。

Windows 局域网 `192.168.3.189:22` 已通过固定的 Tailscale 主机公钥验证，是同一台宿主机。拟议的专用 Ubuntu SSH 转发脚本 `scripts/expose-ubuntu-ssh.ps1` 尚未执行；新增持久端口和防火墙规则被自动审批拒绝，等待用户明确授权。现有 SSH 中转仍可用于部署。

## 工具复用

`scripts/hyperv-console.ps1` 只读抓取指定虚拟机控制台，返回 base64 PNG，用于远程核实当前屏幕。截图放 `runtime/`，不进入 Git。

`scripts/hyperv-type.ps1` 按虚拟按键逐字发送 ASCII，避免 Hyper-V 的 TypeText 在 Linux 控制台输入错字。仅在观察当前控制台后使用；默认不发送回车，必须显式指定 `-Enter` 才执行。服务器运行和恢复步骤见 `server-operations.md`。

制作安装配置盘需要 `pip install -r requirements-provisioning.txt`，然后：

```powershell
python scripts/build_ubuntu_seed.py --public-key "$env:USERPROFILE/.ssh/knowledge_manager_ed25519.pub" --mac-address 00:15:5d:03:17:03 --output runtime/ubuntu-seed.iso
```

生成器只创建 ISO，不自动启动安装。**自动安装会格式化选定磁盘；仅可用于已经实查无数据的目标空盘，不能直接复用于有资料的虚拟机。** 不将真实登录密码写入任何配置。

## 后续验证顺序

1. 完成主应用和虚拟机重启验收，确认 DHCP 地址变化后仍能通过固定主机密钥安全管理，且应用、备份 timer 和微信关闭状态按预期恢复。
2. 为两次已验证备份配置第二物理介质，并定期重复独立恢复演练。
3. 等待 Whisper 主模型续传完成，按固定大小和 SHA-256 校验后发布，在这台无 AVX2 的实际 CPU 上验收中文媒体转写性能与兼容性。
4. 等串行队列实际完成 Qwen 1.5B 下载，并完成 llama.cpp 编译后，先做真实 CPU 推理质量和资源验收再启用 AI。微信凭据齐全后再启用并做端到端验收。
5. 另行设计并验证手机或外网客户端的受控入口与 Obsidian 同步；临时本机 SSH 预览不替代这些工作。

微信入口待用户实际公众号/微信客服账号配置。Obsidian Vault 是服务器上的文件夹；完整客户端访问方案和备份介质仍需部署后验证，不能把微信页面入口视为已完成 Obsidian 同步。
