# Obsidian 远程同步

Ubuntu Vault `/srv/knowledge-manager/vault` 是唯一主数据源。数据库、任务队列、API 密钥、模型缓存和备份归档永远不加入同步文件夹。同步链路只经过 Tailscale 私网。

## 已完成

- Ubuntu Syncthing 服务已启用。
- Windows 已安装 Syncthing，使用 `C:\Users\Administrator\Documents\Obsidian\KnowledgeVault`。
- Ubuntu 与 Windows 已通过 Tailscale 直连并完成 Vault 文件同步。
- 公共发现、Relay、NAT 和局域网发现配置关闭；设备地址使用 Tailscale 地址。

## Android 配对

1. 在 Android 安装 Tailscale 和 Syncthing-Fork，加入同一个 Tailnet。
2. 打开 Syncthing-Fork 的设备信息，取得 Android 设备 ID；不要把设备 ID 或截图发到聊天。
3. 在 Ubuntu 上用 Android 设备 ID 重新运行配置脚本，并指定 Android 的 Tailscale 地址：

   ```sh
   sudo sh /opt/knowledge-manager/current/scripts/install_syncthing_ubuntu.sh \
     --windows-device-id '<已配置的 Windows 设备 ID>' \
     --windows-address 'tcp://100.64.186.105:22000' \
     --android-device-id '<在 Ubuntu 当前终端输入 Android 设备 ID>' \
     --android-address 'tcp://<Android Tailscale 地址>:22000'
   sudo systemctl restart syncthing@knowledge-manager.service
   ```

4. Android 端只接受 `Knowledge Vault` 文件夹，目录中保留 `.stignore`。不要把 `/srv/knowledge-manager` 的上级目录作为共享目录。
5. 用 `scripts/check_syncthing.py` 检查文件夹路径、设备列表、版本保留和冲突状态。

`.obsidian/workspace*.json`、缓存、回收站和 Syncthing 标记文件被忽略；Markdown、附件和其他 Obsidian 设置保留。
