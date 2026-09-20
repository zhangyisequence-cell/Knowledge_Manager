# Obsidian 远程同步

Ubuntu Vault `/srv/knowledge-manager/vault` 是唯一主数据源。数据库、任务队列、API 密钥、模型缓存和备份归档永远不加入同步文件夹。同步链路只经过 Tailscale 私网。

## 已完成

- Ubuntu Syncthing 服务已启用。
- Windows 已安装 Syncthing，使用 `C:\\Users\\Administrator\\Documents\\Obsidian\\KnowledgeVault`。
- Ubuntu 与 Windows 已通过 Tailscale 直连并完成 Vault 文件同步。
- 公共发现、Relay、NAT 和局域网发现配置关闭；设备地址使用 Tailscale 地址。

## 鸿蒙手机

同步方式取决于鸿蒙版本：

- **支持 Android 应用的 HarmonyOS 版本**：在手机上安装可用的 Tailscale Android 客户端和 Syncthing-Fork，登录同一个 Tailnet 后按下面的配对步骤操作。只从可信来源安装，并确认手机上的 Obsidian 使用同步后的 Vault 目录。
- **HarmonyOS NEXT 或无法运行 Android 应用的版本**：不能直接使用当前 Tailscale + Syncthing-Fork 链路。不要把 Android APK 当作已验证方案；这时手机只能作为后续的网页入口使用，Obsidian Vault 仍由 Ubuntu 和 Windows 保持同步，直到确定有可用的鸿蒙原生客户端。

## Android/兼容鸿蒙设备配对

1. 在手机安装 Tailscale 和 Syncthing-Fork，加入同一个 Tailnet。
2. 打开 Syncthing-Fork 的设备信息，取得设备 ID；不要把设备 ID 或截图发到聊天。
3. 在 Ubuntu 上用设备 ID 重新运行配置脚本，并指定手机的 Tailscale 地址：

   ```sh
   sudo sh /opt/knowledge-manager/current/scripts/install_syncthing_ubuntu.sh \
     --windows-device-id '<已配置的 Windows 设备 ID>' \
     --windows-address 'tcp://100.64.186.105:22000' \
     --android-device-id '<在 Ubuntu 当前终端输入手机设备 ID>' \
     --android-address 'tcp://<手机 Tailscale 地址>:22000'
   sudo systemctl restart syncthing@knowledge-manager.service
   ```

4. 手机端只接受 `Knowledge Vault` 文件夹，目录中保留 `.stignore`。不要把 `/srv/knowledge-manager` 的上级目录作为共享目录。
5. 使用部署环境的 Python 检查文件夹路径、设备列表、版本保留和冲突状态：

   ```sh
   /opt/knowledge-manager/current/.venv/bin/python \
     /opt/knowledge-manager/current/scripts/check_syncthing.py \
     --config /var/lib/knowledge-manager/.config/syncthing/config.xml \
     --vault /srv/knowledge-manager/vault
   ```

`.obsidian/workspace*.json`、缓存、回收站和 Syncthing 标记文件被忽略；Markdown、附件和其他 Obsidian 设置保留。
