# CentOS 服务器部署

Knowledge Manager 的目标服务器是旧笔记本上的 CentOS Stream 或兼容 RHEL 的系统。微信只负责提交资料和接收处理结果；原件、SQLite、Obsidian Vault、备份和处理队列都保存在服务器本地。

## 支持范围

`scripts/install_centos.sh` 支持 CentOS Stream/RHEL-compatible 9 或 10，并要求 `python3.12`。CentOS 需要启用 EPEL；脚本会尝试启用 EPEL 和 CRB，然后安装 FFmpeg、antiword、Tesseract 中文/英文语言包和 Python 构建依赖。若仓库没有 `python3.12`、`ffmpeg-free` 或 `antiword`，脚本会停止，不会启动半配置服务。

先确认系统版本和 Tailscale：

```sh
cat /etc/os-release
tailscale ip -4
```

## 安装

把仓库发布归档解压到 `/opt/knowledge-manager/releases/<version>`，再执行：

```sh
sudo sh scripts/install_centos.sh
```

安装目录：

| 路径 | 用途 |
| --- | --- |
| `/opt/knowledge-manager/releases/<version>` | 不可变代码发布目录 |
| `/opt/knowledge-manager/current` | 当前发布软链接 |
| `/srv/knowledge-manager/vault` | Obsidian Markdown、原始附件和转写文本 |
| `/srv/knowledge-manager/data` | SQLite、任务状态和处理缓存 |
| `/srv/knowledge-manager/model-cache` | Whisper 等可重新下载的模型缓存 |
| `/etc/knowledge-manager/server.env` | 0600 的本机配置和凭据 |
| `/var/backups/knowledge-manager` | 带校验清单的备份归档 |

默认 `AI_ENABLED=false`、`WECHAT_ENABLED=false`。先在服务器本地填写 DeepSeek 配置，运行 `scripts/check_deepseek_ingestion.py` 验证正文、表格、OCR 和音视频转写文本的分段、证据和 Obsidian 写入，再启用 AI。原始 PDF、Word、Excel、图片、音频和视频不会发送给 DeepSeek。

## Obsidian 与外网

只把 `/srv/knowledge-manager/vault` 通过 Tailscale 私网 Syncthing 同步到 Windows 和安卓；数据库、凭据、原始数据目录和备份不共享：

```sh
sudo sh scripts/install_syncthing_centos.sh \
  --windows-device-id '<Windows设备ID>' \
  --android-device-id '<Android设备ID>'
sudo sh scripts/install_syncthing_centos.sh --check-only
```

确认设备 ID、文件哈希和冲突策略后，才启用 `syncthing@knowledge-manager.service`。Cloudflare Tunnel 只代理 `/wechat/callback`，不暴露管理页面、SQLite 或 Vault；没有真实微信客服/公众号凭据和 HTTPS 回调验收前保持关闭。

## 数据保存与恢复

备份服务每小时暂停微信回调、Syncthing 和主应用，归档 Vault、附件、SQLite 与本地配置，校验 SHA-256 后按 24 小时、30 日和 12 周保留。恢复前先验证归档：

```sh
sudo /opt/knowledge-manager/current/.venv/bin/python \
  /opt/knowledge-manager/current/scripts/server_backup.py verify \
  /var/backups/knowledge-manager/knowledge-<时间>-<哈希>.tar.gz
```

CentOS 开启 SELinux 时，安装脚本会为 `/srv/knowledge-manager` 恢复 `var_lib_t` 标签；发生拒绝时先查看 `ausearch -m avc -ts recent`，不要关闭 SELinux 作为绕过方案。备份仍需复制到另一块物理盘或外部设备，单机双盘不能抵御整机损坏。

## 验收顺序

1. `/api/health` 返回 `status=ok` 且 `storage_configured=true`。
2. 手动提交文字、网页、PDF、Word、Markdown、Excel、图片、音频和视频，核对原件、SQLite、Markdown 和附件链接。
3. 配置 DeepSeek 后运行真实长文验收，确认正文/表格/OCR/转写文本才出站。
4. 完成备份、校验、恢复和 CentOS 重启演练。
5. 最后配置微信回调和 Cloudflare，并验证文字、链接、文件、语音、视频收发及重复通知幂等。

真实密钥、微信账号、Cloudflare token、设备 ID 和个人资料只能留在 CentOS 本地，不能提交 GitHub。
