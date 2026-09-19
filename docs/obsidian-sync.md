# Obsidian 远程同步

服务器 Ubuntu Vault `/srv/knowledge-manager/vault` 是唯一主数据源。数据库、任务队列、API 密钥、模型缓存和备份归档永远不加入同步文件夹。同步链路只经过 Tailscale 私网，Syncthing 的公共发现、Relay 和 NAT 穿透已关闭。

## 首次配对

1. 在 Windows 和安卓手机安装 Tailscale，加入同一个账号并确认能看到服务器的 Tailscale 地址。
2. Windows 安装 Syncthing 与 Obsidian；安卓安装 Syncthing-Fork 与 Obsidian。先不要在客户端创建第二个 Vault。
3. 在服务器、Windows 和安卓分别打开 Syncthing，互相交换设备 ID。服务器只接受明确提供的 Windows 与 Android 设备 ID。
4. 服务器共享名为 `Knowledge Vault` 的 Send & Receive 文件夹；Windows 选择 Obsidian Vault 目录，安卓选择本地专用 Vault 目录。两端都接受 `.stignore` 后再开始同步。
5. 用一份测试 Markdown 和一个附件分别验证服务器→客户端、Windows→服务器、安卓→服务器的文件哈希一致，再打开日常使用。

`.obsidian/workspace*.json`、缓存、回收站和 Syncthing 标记文件被忽略；Markdown、附件和其他 Obsidian 设置保留。不要把 `/srv/knowledge-manager` 的上级目录作为共享目录。

## 冲突和离线编辑

服务器端启用 30 天 staggered versioning。两台设备离线同时修改同一 Markdown 时，先保留 Syncthing 产生的冲突文件和服务器历史版本，再人工合并；不要直接覆盖冲突文件。服务器 Vault 与经过校验的备份是恢复依据。

## 检查

服务器执行配置检查时只提供本地 API key，不把 key 或设备 ID 写入日志：

```sh
sudo /opt/knowledge-manager/current/.venv/bin/python scripts/check_syncthing.py \
  --config /var/lib/knowledge-manager/.config/syncthing/config.xml \
  --vault /srv/knowledge-manager/vault
```

检查报告必须显示只有一个 Vault 文件夹、Send & Receive、30 天版本保留、Tailscale 私网管理和无冲突。设备离线、文件哈希未收敛或出现意外共享路径都视为未通过。
