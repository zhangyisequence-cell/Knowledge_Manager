# 服务器运行与恢复

目标 CentOS Stream/RHEL-compatible 9 或 10，入口先只监听 `127.0.0.1:8787`。以下脚本已通过本地测试，真实服务器验收结果单独记录在 `centos-deployment.md`，不能将脚本存在视为部署完成。

## 目录和部署

| 路径 | 用途 |
| --- | --- |
| `/opt/knowledge-manager/releases/<version>` | 每次发布的代码及独立 Python 环境 |
| `/opt/knowledge-manager/current` | 当前版本链接 |
| `/srv/knowledge-manager/vault` | Obsidian Markdown 与原始附件 |
| `/srv/knowledge-manager/data` | SQLite、任务状态、解析中间文件 |
| `/srv/knowledge-manager/model-cache` | 可重新下载的模型，不进数据备份 |
| `/etc/knowledge-manager` | 仅在服务器配置的运行设置和凭证 |
| `/var/backups/knowledge-manager` | 本机备份及 SHA-256 清单 |

将 Git 发布归档解压到新的 release 路径后，执行 `sudo sh scripts/install_centos.sh`。不要将真实资料、模型、运行配置或密钥提交到 Git。安装脚本保留既有数据和配置，先核验候选服务配置、暂停备份调度和所有写入者、取得备份锁并创建已验证归档，再切换程序。主应用健康检查通过后才启动已配置的微信回调。

激活失败会恢复此前程序链接与服务文件，停止并禁用应用、回调和备份定时器，防止重启后旧代码自动操作可能已迁移的数据。先按错误中给出的升级前归档进行恢复演练并核对数据库；确认兼容性或恢复到旧数据后，显式重新启用相应服务。涉及数据库结构变化时不能仅靠代码回退。正式部署、正常版本升级和独立数据恢复已实测；当前仍须完成部署后的整机重启等验收，具体证据见验证记录。

服务以 `knowledge-manager` 专用账户运行，默认关闭 AI。实际模型配置及处理结果验证通过后才启用 AI。服务正常运行不代表微信、AI 或音视频识别已验收。

## 临时打开本机预览

在已接入该服务器 Tailscale 网络的 Windows 电脑上，可通过既有的宿主机与 Ubuntu SSH 密钥临时打开仅限本机访问的网页隧道。先准备包含宿主机和 Ubuntu 已核验公钥的 `known_hosts` 文件，然后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\open_server_preview.ps1 -Open -KnownHostsPath C:\path\to\known_hosts
```

浏览器访问 `http://127.0.0.1:18787`。脚本从宿主机只读查询虚拟机网卡邻居记录，确认唯一地址后在当前窗口运行 SSH；按 `Ctrl+C` 或关闭窗口即停止。若端口已占用，使用 `-LocalPort` 选择其他 1024–65535 端口。密钥默认读取当前用户的 `.ssh\knowledge_manager_ed25519`，也可用 `-KeyPath` 指定。为兼容 Windows PowerShell 5.1 的嵌套 SSH 参数，脚本会把用户提供的公开主机指纹覆盖复制到 `%LOCALAPPDATA%\KnowledgeManagerPreview\known_hosts`；它不会复制私钥、修改 SSH 配置、开放防火墙或建立持久转发。不带 `-Open` 时只显示计划，不连接远端。

## 备份

每小时整点执行定时备份。备份开始前会按固定顺序暂停微信回调、Syncthing 和主应用；只恢复备份前原本处于运行状态的服务。可手动运行：

```sh
sudo systemctl start knowledge-manager-backup.service
sudo journalctl -u knowledge-manager-backup.service -n 30 --no-pager
```

备份先暂停微信回调，再暂停 `syncthing@knowledge-manager.service`，最后暂停应用，归档 Vault、附件、数据库和配置，核对每个文件的哈希后发布压缩包。保留策略保留最近 24 个小时点、30 个日点、12 个周点评级、升级标记归档和最新已验证归档；没有校验清单的文件不会被删除。完成、失败或普通取消后按原状态恢复；原先关闭的服务保持关闭。断电或强制终止无法保证执行清理，应在开机后检查服务状态。新增写入程序必须通过 `--companion-service` 纳入暂停范围。

Syncthing 以 `syncthing@knowledge-manager.service` 运行，只共享 `/srv/knowledge-manager/vault`，通过 Tailscale 配对 Windows 与安卓。CentOS 使用 `scripts/install_syncthing_centos.sh` 安装和渲染配置；启用前运行 `scripts/check_syncthing.py`，确认设备、文件哈希、版本保留和冲突状态；备份会先暂停该服务。微信回调使用独立的 `knowledge-manager-wechat.service`，仅监听 `127.0.0.1:8766`。Cloudflare Tunnel 只转发 `/wechat/callback`，最终规则为 404，不能把管理面或 Vault 作为 upstream。先用 `scripts/check_public_entry.py` 检查本地配置，再启用 `cloudflared-knowledge-manager.service`。默认没有 `/etc/knowledge-manager/wechat.enabled` 标记且 `WECHAT_ENABLED=false`，不会启动回调。填妥服务器本地 `server.env` 中的官方账号设置及白名单，完成配置校验后，创建标记文件并启用回调服务；主应用重启后加载后台收发 worker。回调关闭访问日志，避免验签参数进入请求日志。反向代理亦应关闭该路径的查询参数日志。没有真实账号的收发验收不能视为接通微信。

配置可能含凭证，因此备份保持私有权限。Windows 宿主机现以专用 SYSTEM 任务，每小时把最新完整归档复制到 `D:\KnowledgeManagerBackups`，校验大小和 SHA，并保留至少 5 GiB 空闲空间。D: 和 Ubuntu 所在 E: 已确认为不同物理硬盘，且从 D: 副本恢复的实机演练通过。参见 [第二盘备份](second-disk-backup.md)。两块盘仍在同一台电脑内；整机丢失或损坏需要另行设置外部备份。

## 恢复演练

使用实际归档文件名替换示例，目标必须为尚不存在的新路径：

```sh
sudo /opt/knowledge-manager/current/.venv/bin/python /opt/knowledge-manager/current/scripts/server_backup.py verify /var/backups/knowledge-manager/knowledge-EXAMPLE.tar.gz
sudo /opt/knowledge-manager/current/.venv/bin/python /opt/knowledge-manager/current/scripts/server_backup.py restore /var/backups/knowledge-manager/knowledge-EXAMPLE.tar.gz /srv/knowledge-manager-restore-EXAMPLE
```

恢复程序检查清单、路径安全、文件散列及 `data/*.sqlite` 完整性，保留 Vault 内的相对附件链接。它不会覆盖现有服务器目录或自动切换生产数据。恢复出的文件默认归执行者所有，root 执行时应用尚不能读取。

若需要正式恢复，先停止应用和备份定时器，保留当前数据目录用于回退。将恢复的 `vault`、`data` 放回上述标准路径，再赋予 `knowledge-manager:knowledge-manager` 所有权；配置由 root 持有，`server.env` 权限 0600，配置目录 `root:knowledge-manager` 权限 0750。备份中的旧任务可能引用原服务器绝对路径，因此必须保持原路径或进行经过验证的迁移。模型缓存可保留或重新下载。

恢复后先检查健康接口、数据库数量、中文笔记和实际附件链接，再恢复定时器。定期进行真实文件上传、重启和恢复演练，并记录日期、版本、归档哈希和结果。

## 已执行的基础验证

2026-09-18：本地 71 项测试、Ruff 和依赖检查通过；GitHub 提交 `eb9582b` 的 Windows/Ubuntu CI 均通过。在 Ubuntu 24.04 安装环境使用 `sudo python3 scripts/check_backup_linux.py` 创建独立临时服务和合成文件，实测 systemd 取消备份后服务恢复、未完成归档清理。脚本只使用新建的测试目录和专用测试服务，不操作生产数据。这项结果不替代最终服务器的真实知识库恢复与重启验收。
