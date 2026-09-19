# 2026-09-19 验证记录

## 当前 DeepSeek 验收边界

本分支已加入 provider-neutral 的 `scripts/check_deepseek_ingestion.py`。它要求服务器本地配置 `AI_ENABLED=true`、`OPENAI_MODEL`、`KNOWLEDGE_OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，提交超过 9,000 字的合成资料，轮询真实队列，并校验连续分段、开头/中段/结尾事实、逐字证据、SQLite 原文和 Obsidian 元数据。输出只保留模型、提供方、字符数、哈希、覆盖和事实布尔值，不保存正文、密钥或服务器路径。当前尚未在 Ubuntu 提供 DeepSeek 密钥，因此没有真实 API 通过证据；下文关于 Qwen 的记录是历史实验，不能替代 DeepSeek 验收。

## 17:54 实机增量

- release `a849d7f` 已于 05:23:35 激活，Whisper 固定快照已校验并发布到应用缓存。真实 HTTP 上传 WAV、MP4、AMR 的三项任务于 05:37:17 全部完成；各项原件、Vault 附件、SQLite 转写、笔记转写与模型来源一致，复核提示存在。报告 `/var/tmp/km-media-http-a849d7f/media-ingestion-results.json` 中 `pipeline_passed=true`，`transcription_accuracy_passed=false`；严格事实核对每项仍为 4/5，均未正确识别“归档”。验收单元退出 1 是准确性未达标，三项入库任务本身均 succeeded。
- 新媒体数据已备份为 `knowledge-20260918T214128-70b57f76.tar.gz`，2,000,989 字节；05:42 第二盘复制任务返回 0、状态 `copied`。Ubuntu 原包与 D: 副本独立 SHA-256 均为 `380025c5a189a6e32ad23fb55fb194818dd4857025a67d9378971e89ed48444a`。备份后健康接口恢复 `status=ok`、`storage_configured=true`，应用与备份 timer 均 active。
- llama.cpp 固定源码构建成功，实际 `--version` 可运行、动态库均可解析；构建峰值约 1.6 GiB、swap 0。Qwen 下载曾因镜像提前结束而保留 1,031,405,640 字节断点；服务端单字节 Range 检查返回正确 `206` 和总大小，随后同一单元续传完成。最终文件 1,117,320,736 字节，SHA-256 为 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`，经临时缓存复验后原子发布到服务账户目录。
- `llama-server` 只监听 `127.0.0.1:8080`，未启用开机启动。健康和模型列表通过；合成 JSON 请求在 16.108 秒返回完整对象，峰值约 197 MB，swap 保持 12 KB 基线。完整 9,641 字四段入库首轮按预期暴露默认 180 秒不足：首段推理未完成即 `ReadTimeout`，没有 item 或笔记。
- 将本地请求超时提高到 600 秒后，用完全相同的 9,641 字样本复验。四段均完成且没有截断，单段耗时分别约 438、523、492、161 秒，LLM 峰值约 323 MB、swap 不增长；任务和笔记成功保存。但严格检查只找到开头的 `KJ-2026-104 / 林岚`，遗漏中段 `KJ-2026-287 / 23741元` 及尾段 `KJ-2026-953 / 2026年10月17日`，`passed=false`。失败证据在 `/var/tmp/knowledge-local-ai-acceptance-20260919-1745`。应用随即恢复 `AI_ENABLED=false`，健康接口确认 AI 关闭；LLM 已停止、disabled，启用标记已删除。模型文件留作可复现实验，不能称为合格的正式总结模型。
- 本轮源码统一验证为 59 项上游测试及 334 项项目测试通过，共 393 项；Windows 因符号链接权限跳过 4 项，Ruff 和依赖检查通过。远端提交 `8eeea3d2f87821ea8d82bf243c955bd7fbfef985` 的 GitHub Actions 运行 `35435755348` 成功。
- release `/opt/knowledge-manager/releases/bfc579a` 于 18:17 激活，升级前验证备份为 `knowledge-20260919T101709-622e759b.tar.gz`。新版本真实上传合成 WAV 的任务 `d4b1088a74464a0b941ed52656073486` succeeded；Obsidian frontmatter 与 SQLite 均为 `source_type=local_file`，自动转写复核标记存在，规则模式未被误称为 AI。
- 最新归档 `knowledge-20260919T102200-d4662252.tar.gz` 为 3,086,652 字节。Ubuntu 原件与 Windows D: 副本 SHA-256 均为 `53aef3626492edd6ca659b7aedfbbaaf488b0a3d136ca22b48a3e89276aaa0a4`，第二盘任务结果为 0、状态 `copied`。
- 18:26 完成虚拟机重启。Hyper-V 状态 Running、心跳正常；DHCP 地址从 `172.27.65.231` 改为 `172.27.67.99`，仍通过固定 SSH 主机指纹登录。重启后 `current` 指向 `bfc579a`，健康接口 `status=ok`、`storage_configured=true`、`ai_enabled=false`；应用和备份 timer active/enabled，LLM 与微信 inactive/disabled，两个启用标记均不存在，swap 为 0。
- 临时预览脚本在重启后自动发现新地址，环回页面返回 HTTP 200、27,287 字节。Windows PowerShell 5.1 为嵌套 SSH 使用固定的用户级公开主机指纹缓存 `%LOCALAPPDATA%\\KnowledgeManagerPreview\\known_hosts`；真实打开/关闭后缓存与用户提供的可信文件 SHA-256 一致，没有随机临时文件残留。缓存不含私钥或凭证。

下文为分阶段记录；较早的“尚未完成”状态以较新的实机增量为准。全部样本均为合成验收资料。

上一轮统一验证：59 项上游测试、320 项项目测试通过，共 379 项；Windows 因符号链接权限跳过 4 项，Ruff 全部通过。新增备份导出器的 3 项符号链接场景已在 Ubuntu 目标实际执行通过。该结果已由根代理独立运行确认；以下历史记录用于说明验证范围，不能把测试替身当成真实账号或正式服务器验收。

05:15 新增实机结果：

- 第二块物理盘备份已部署。Ubuntu 的 VHDX/AVHDX 位于 E:（Disk 1），备份位于 Windows D:（Disk 0）。`KnowledgeManager-SecondDiskBackup` 以 SYSTEM 每小时运行，05:05 首次复制、05:10 定时复用均返回 `LastTaskResult=0`。归档 `knowledge-20260918T200601-bda375a6.tar.gz` 为 517,611 字节，D: 独立 SHA-256 为 `3166e990f183aeb2731394ed55ad6213d9f5bf7abd955fe44b24e5e3998cdb32`。
- 从该 D: 文件实际取回并核对同一 SHA，送回 Ubuntu 后再次核对；验证 67 个文件并恢复到 `/var/tmp/km-restored-from-disk0-20260919-0514`。恢复数据库 `integrity_check=ok`、23 个条目、23 个 succeeded 任务，恢复目录包含 25 个 Markdown 文件。最初的一次流式传输因 60 秒超时只收到部分数据，散列检查拒绝该副本且没有执行恢复；最终演练使用随后完整取回并校验过的 D: 副本。
- 新专用备份密钥的源文件及安装副本、D: 备份目录均实查只授予 SYSTEM/Administrators 访问。`km-backup` 实际 `latest` 请求成功，`id` 请求被拒绝，`-W` 转发返回 administratively prohibited。没有新增 SSH 端口、转发或防火墙规则。导出器目标测试 30/30 通过；Windows 复制测试 7 项通过，覆盖真实二进制、超时/截断/散列错误、拒绝覆盖及隐藏目录。对应修复远端提交 `d1e83d58e937bd2bd87ef75a4711215c26b57c5f` 的 CI run `35395185216` 成功。
- Whisper 固定快照现已在 Ubuntu 下载完成并校验。真实 WAV/MP4/AMR 共耗时 152.63 秒，单元峰值内存约 1.0 GiB、swap 峰值 0；每个样本仍为 4/5，均把“归档”识别为“归到/歸道”，`passed=false`。证据 `/var/tmp/km-media-879926c/chinese-transcription-result.json`。不能把能够推理说成准确率验收通过；此时尚未发布到应用模型缓存。
- llama.cpp 首次 Git 获取因 curl 56 超时、early EOF 失败，尚未进入编译。固定官方源码归档已按 37,437,459 字节及 SHA-256 `03fb04316eb32a7b7347004a79a8dd60531c02f5a064d853fed3b53828951723` 在两端校验，新的离线源码构建单元已于约 05:00 进入编译，05:15 为 79%。Qwen 下载正在继续，尚无实际语言模型推理结果。

新增自动转写来源与复核标记代码、真实 AI 长文验收脚本已通过上述本地检查及独立审查，但这里尚不声称已更新正式应用或通过真实 AI 长文验收。转写标记不修改文字、解码、模型、原件或 4/5 的严格结果；长文验收不接受规则回退、缺段、仅在 claim 中出现却没有原文引用的事实，或与 SQLite 不一致的笔记标记。

本地完整检查：35 项上游测试、179 项项目测试全部通过，Ruff 与 pip 依赖检查通过。仅有测试依赖现存两条弃用提示。以下是验证边界，不等同于服务器全系统验收。

后续加入旧版 DOC 后：35 项上游 + 189 项项目测试（合计 224）与 Ruff 通过。微信基础及解析修复提交 `a265529` 的 GitHub Windows/Ubuntu CI 均通过。

已成功激活的部署源码本地提交为 `2a76c089e3c1c773b291f129911d4ddd000b5f93`，远端提交为 `5cafe99bb70706a7e14fe37a16a07a263e2ff45a`，两者 tree 均为 `36762501b1edee5e33d006bf6d08f256eb4da330`；对应的两次 CI 均成功。

中文 PDF 修复版的本地提交为 `879926ca190444dd49d91eb3a1b20342e132ef2e`，远端提交为 `6aa0cb1f9882c37dfb6630aa23d9a0465dc48f5e`，共同 tree 为 `f654051995799804d0c7984af5dc9f58adb60190`；新归档 SHA-256 为 `747327e5eab3bdd971a6edc2d10bdb391031d36e956353b878624ff6b881af50`。CI run `35388693783` 的 Windows 和 Ubuntu job 均已独立查询为成功。release `879926c` 已于 03:59:49 正式激活，更新单元结果为 success/inactive，`current` 已指向新 release；激活前备份为 `knowledge-20260918T195944-2962f908.tar.gz`。

- PDF 混合页：服务器真实 OCR 已正确识别扫描页中的金额、李明、日期和“归档”，空白页也正确处理。随后发现同一实际 PDF 的中文可复制文字层采用 Type0 `/UniGB-UTF16-H` 且没有 `ToUnicode`，pypdf 提取会乱码；现已改用 PyMuPDF。新 release 上的独立实际 Tesseract 检查 7 项均为 true、退出码为 0，证据为 `/var/tmp/km-acceptance-879926c/ocr/chinese-ocr-result.json`。HTTP 上传任务 `e29b8170bcc8466aaf6289ccae7fdae6` 成功；产物核对中，可复制中文、12800、李明、“知识归档”、空白页标签、规则模式和标签、服务端原件及 Vault 附件 SHA 均为 true，日期去空格后匹配，两个附件分别为原 PDF 和扫描页图片。证据为 `/var/tmp/km-acceptance-879926c/ocr/server-ocr-ingestion.json`。含大量文字层的单页仍可能有图片内文字未额外识别。
- 媒体：纯音频无转写时，启用视觉模型也不能显示成功；常用音频与 AMR 的路由和转写格式一致。SILK 明确拒绝。29 项回归测试通过。
- 中文真实媒体：Huihui 合成约 18.4 秒中文 WAV，FFmpeg 生成 MP4；另生成 AMR。三者均完成实际 CPU/int8 推理，金额 12800 元、李明、周五复盘、下周三均准确；“归档”却识别为“归到/歸道”。严格检查每份为 4/5，总结果 `passed=false`，不将近音字算作正确。样本及完整转写放在忽略的 `runtime/samples`，未使用答案提示。这个结果来自当前管理电脑，旧笔记本上的速度与质量仍须复验。
- 任务恢复：模拟真实 SQLite 保存失败，确认笔记已经写出后重试同一任务，只保留一份笔记和原件，最终数据库一条记录。内容 ID 和创建日期沿用持久化任务。
- 微信：公开官方加密向量与独立合成向量、真实加密 HTTP 回调及 SQLite、分页/断线/去重测试通过。审查发现的受理消息挡住结果、重叠同步提前结束问题已先复现再修复。
- 旧 DOC：通过 Word COM 生成真正 Word 97–2003 二进制样本，上传 Ubuntu 安装环境后用实际 Antiword 0.37 提取；“负责人李明”、表格“资料采购 / 12800 / 2026-09-30”和“周五完成归档核对”全部保留。最终样本移除文档个人信息后再次上传实测，SHA-256 为 `a4f5471683d6f8af62d2ff7a497b3db348994c6dd27c87d1bd351335db6bff1c`，与本地一致；独立 OLE 元数据读取确认作者/保存者/公司/经理字段为空或通用占位值。解析为纯文本，不保证 Word 排版；带图无文字、损坏、加密及超限文件明确失败。目标服务器的完整上传入库链路仍待部署后复验。

`scripts/check_chinese_transcription.py` 是真实模型验收入口，固定 `Systran/faster-whisper-small` 快照 `536b0662742c02347bc0e980a01041f333bce120`。模型已在本地管理电脑完整下载，主文件 SHA-256 为 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`；这不表示服务器已有完整模型。服务器端三个元数据文件已校验，主模型正在直接断点续传，尚未完成或发布。下载器核对公开仓库的文件大小、Git blob/LFS 散列，支持有界断点续传并原子发布。镜像曾对 Python 默认 User-Agent 返回 HTTP 403，改用项目 User-Agent 后可下载；下载器测试现为 13 项并通过，新增覆盖完整 `.part` 无网络发布及损坏的完整 `.part` 重新下载；配套 Qwen 测试 4 项，合计 17 项。完整统一测试总数仍为 317。应用新增指定离线快照与复用加载的设置；指定路径不存在时不偷偷下载替代模型。目标 CPU 的 CTranslate2 兼容查询报告支持 `float32`、`int16`、`int8`、`int8_float32`，但这不是实际转写验收。

备份新增暂停独立微信回调，再暂停主应用；恢复时仅恢复原先运行的服务。11 项本地备份测试通过；Ubuntu 临时安装环境使用两个独立测试服务，真实 systemd 取消后两个服务均恢复且没有遗留未完成归档。服务器首次正式备份 `knowledge-20260918T194804-800684b8.tar.gz` 已创建并验证 23 个文件，独立恢复到 `/var/tmp/km-restore-20260919-0348` 后 SQLite `integrity_check` 为 `ok`，包含 7 个条目和 7 个成功任务。

04:06:02，新 release 的正式备份 `knowledge-20260918T200601-bda375a6.tar.gz` 成功完成并验证 67 个文件；恢复到 `/var/tmp/km-restore-879926c-0406` 返回退出码 0，独立 SQLite `integrity_check` 为 `ok`，包含 23 个条目和 23 个成功任务。恢复后主应用和备份 timer active、微信 inactive；健康接口为 `status=ok`、`storage_configured=true`、AI 关闭。备份重启应用后立即 curl 的一次连接拒绝发生在服务尚未启动完成时，随后健康正常，不视为持续失败。第二物理介质仍未配置。

AI 契约补齐：原先会截取前 80,000 字符，现在用连续 3,000 字符窗口逐段处理并保留覆盖范围；配置模型上下文应至少为 8,192 token，字符窗口不等于准确 token 计数。分段提取结果确定性合并；引用须在原文逐字存在，保存字符位置和来源 URL，不成立的引用剔除并显示数量。关闭 AI 时笔记明确写“规则提取（未启用 AI）”。空 JSON、缺字段、字段类型错误或输出未正常结束均失败，不伪装成智能分析完成。11 项 AI 契约测试通过，使用合成响应，尚未验证实际本地语言模型的输出质量。

`scripts/check_server_ingestion.py` 已在正式服务器完成七类真实入库：中文文本、TXT、Markdown、含表格 DOCX、多工作表并保留公式标记的 XLSX、英文 PDF 和旧 DOC 全部通过，任务终态、原始正文、文件模式以及原件和附件 SHA 均已核验。新 release `879926c` 激活后七类复验仍全部通过，证据为 `/var/tmp/km-acceptance-879926c/server-ingestion-results.json`。公开 SQLite `about.html` 也已抓取入库，已核对笔记中保留的来源 URL 与正文，整份笔记为 5,193 字节。

Ubuntu 24.04.4、release `2a76c08` 及其后续修复版 `879926c` 已完成安装和激活。基础健康、七类入库、独立 Tesseract 检查、中文混合 PDF 的 HTTP/Vault 产物核对及 23 条目独立恢复已有上述实机证据。临时预览仅通过既有 SSH 绑定管理电脑 `127.0.0.1:18787` 到来宾 `127.0.0.1:8787`，健康和页面读取成功；没有新增持久端口或防火墙，也不代表手机/外网客户端或 Obsidian 同步完成。新增本地 AI 部署脚本已提交（本地 `22078ba`、远端 `bc923550b872e27b34f91b01dd1af4fb7663d98d`，CI 成功），固定 llama.cpp CPU 源码和 Qwen 1.5B；一次性 Qwen 下载单元仅排队等待 Whisper 完整校验，尚未开始 Qwen 数据传输，无真实 AI 结果。03:59 时 Whisper 下载进度为 287,186,944 / 483,546,902 字节。仍需完成应用和 VM 重启、模型与 AI、微信端到端收发、远程 Obsidian 使用及第二备份介质。
