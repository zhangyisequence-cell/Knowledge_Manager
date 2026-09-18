# 服务器运行与恢复

目标 Ubuntu 24.04，入口先只监听 `127.0.0.1:8787`。以下脚本已通过本地测试，真实服务器验收结果单独记录在 `ubuntu-deployment.md`，不能将脚本存在视为部署完成。

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

将 Git 发布归档解压到新的 release 路径后，执行 `sudo sh scripts/install_ubuntu.sh`。不要将真实资料、模型、运行配置或密钥提交到 Git。安装脚本保留既有数据和配置，先核验候选服务配置、暂停备份调度和所有写入者、取得备份锁并创建已验证归档，再切换程序。主应用健康检查通过后才启动已配置的微信回调。

激活失败会恢复此前程序链接与服务文件，停止并禁用应用、回调和备份定时器，防止重启后旧代码自动操作可能已迁移的数据。先按错误中给出的升级前归档进行恢复演练并核对数据库；确认兼容性或恢复到旧数据后，显式重新启用相应服务。涉及数据库结构变化时不能仅靠代码回退。正式部署及真实升级恢复验收仍待完成。

服务以 `knowledge-manager` 专用账户运行，默认关闭 AI。实际模型配置及处理结果验证通过后才启用 AI。服务正常运行不代表微信、AI 或音视频识别已验收。

## 备份

每日 03:30 附近执行定时备份。可手动运行：

```sh
sudo systemctl start knowledge-manager-backup.service
sudo journalctl -u knowledge-manager-backup.service -n 30 --no-pager
```

备份先暂停微信回调，再暂停应用，归档 Vault、附件、数据库和配置，核对每个文件的哈希后发布压缩包。完成、失败或普通取消后，先恢复应用，再恢复原先运行的回调服务；原先关闭的服务保持关闭。断电或强制终止无法保证执行清理，应在开机后检查服务状态。以后增加同步程序时，也必须通过 `--companion-service` 将其他写入者纳入暂停范围。

微信回调使用独立的 `knowledge-manager-wechat.service`，仅监听 `127.0.0.1:8766`，公网反向代理只能转发 `/wechat/callback`。默认没有 `/etc/knowledge-manager/wechat.enabled` 标记且 `WECHAT_ENABLED=false`，不会启动回调。填妥服务器本地 `server.env` 中的官方账号设置及白名单，完成配置校验后，创建标记文件并启用回调服务；主应用重启后加载后台收发 worker。回调关闭访问日志，避免验签参数进入请求日志。反向代理亦应关闭该路径的查询参数日志。没有真实账号的收发验收不能视为接通微信。

配置可能含凭证，因此备份保持私有权限。当前保留所有历史，不自动删除。应持续检查剩余空间，将已验证归档复制到另一块物理磁盘或外部备份位置，并在该副本上验证。**同一虚拟磁盘内的备份不能防止旧笔记本硬盘损坏；第二介质尚待配置。**

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
