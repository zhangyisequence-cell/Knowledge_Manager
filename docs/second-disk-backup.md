# 第二块物理硬盘备份

Ubuntu 继续按现有定时任务生成一致性备份，覆盖笔记、附件、原件、SQLite 与应用配置。Windows 宿主机每小时检查一次最新完整备份，并将通过校验的归档保存到 `D:\KnowledgeManagerBackups`。历史版本不会自动删除；剩余空间不足 5 GiB 时停止复制并记录失败。

本机检查确认：Ubuntu 的 VHDX/AVHDX 位于 E:（物理 Disk 1），D: 位于物理 Disk 0。因此这份副本可以用于 Ubuntu 所在硬盘损坏后的恢复；两块盘仍在同一台电脑内，整机丢失或损坏需要另行配置外部备份。

## 访问范围

备份使用独立的 `km-backup` 账户与专用 SSH 密钥。宿主机根据 Hyper-V 虚拟网卡的 MAC 查找来宾地址，并核对固定 SSH 主机公钥。沿用来宾已有的 SSH 服务，不新增端口、防火墙规则或管理转发。

来宾的受限命令只接受 `latest` 和 `get <合法备份文件名>`。它先核对归档散列及内部文件清单，再返回元数据或二进制归档。账户不能获得交互终端或通过该密钥建立转发；不能选择任意文件、目录或程序。最新归档损坏时明确失败，不偷偷回退到旧版本。

宿主机收到归档后再次核对大小与 SHA-256，只有一致时才发布最终文件。中断或校验失败只清理本次临时文件，不覆盖已有归档。专用密钥、配置与备份目录仅允许 SYSTEM 和 Administrators 访问，均不提交 Git。

## 首次安装

先在宿主机新的私有目录中生成专用 Ed25519 密钥。不要复用管理员管理密钥。通过已经认证的管理连接，将公钥和项目部署文件送到来宾；从该管理连接读取 `/etc/ssh/ssh_host_ed25519_key.pub`，在宿主机专用 known-hosts 文件中使用固定别名 `knowledge-manager-ubuntu` 保存公钥。

来宾以 root 执行已检查的项目脚本：

```bash
sudo bash scripts/install_backup_exporter.sh --install /path/to/backup_ed25519.pub
```

安装器拒绝已存在的同名账户、组、安装目录、home 或 sudoers 文件。失败后的专用路径需检查实际状态，不能直接覆盖重装。它不修改已有管理账户和 SSH 配置。

宿主机在管理员 PowerShell 中执行：

```powershell
.\scripts\install_host_backup.ps1 -Install `
  -GuestUser km-backup `
  -KeyPath 'C:\path\to\backup_ed25519' `
  -KnownHostsPath 'C:\path\to\known_hosts'
```

专用任务名为 `KnowledgeManager-SecondDiskBackup`，以 SYSTEM 身份执行。安装器拒绝冲突任务或已有非本项目目录。手动运行与检查：

```powershell
Start-ScheduledTask -TaskName 'KnowledgeManager-SecondDiskBackup'
Get-ScheduledTaskInfo -TaskName 'KnowledgeManager-SecondDiskBackup'
Get-Content -LiteralPath 'D:\KnowledgeManagerBackups\status.json'
```

`status.json` 记录最近一次复制、复用或失败结果；它不是成功恢复的替代证明。任务状态、归档 SHA 与实际恢复三者需要分别检查。

## 恢复演练

从 D: 选定完整归档，经已认证的管理连接复制到恢复服务器的暂存目录。使用项目原有恢复工具，目标必须是新的不存在目录：

```bash
sudo python3 scripts/server_backup.py verify /path/to/copied-backup.tar.gz
sudo python3 scripts/server_backup.py restore /path/to/copied-backup.tar.gz /var/tmp/knowledge-restore-new
```

恢复工具验证每个文件的大小、SHA-256 和 SQLite 完整性。另核对笔记、原件与条目数量；确认后才计划正式切换，演练不得覆盖运行中的知识库。

实际安装、复制及恢复结果记录在 `docs/validation-2026-09-19.md`。脚本存在和单元测试通过本身不表示第二盘已部署。
