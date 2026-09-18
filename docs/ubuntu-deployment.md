# Ubuntu 部署记录

最终平台由用户确认：原先提到 CentOS，实际允许使用现有 Hyper-V 的 Ubuntu。本文记录已观察到的事实，不把配置文件存在当作部署完成。

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

当前部署源码为本地提交 `2a76c089e3c1c773b291f129911d4ddd000b5f93`、远端提交 `5cafe99bb70706a7e14fe37a16a07a263e2ff45a`，两者 Git tree 均为 `36762501b1edee5e33d006bf6d08f256eb4da330`；对应的两次最新 CI 均成功。服务器 release 为 `/opt/knowledge-manager/releases/2a76c08`，上传归档 `/var/tmp/knowledge-manager-minimal.tar.gz` 的 SHA-256 `e5097de29df4b569ed3f22ad2a30e21e61ac9ddb02d6bdc407d90e0aa8ea41f3` 已核验。安装器加入 `--no-install-recommends` 后，模拟结果由 205 个包降至 161 个包，下载量由 180 MB 降至 124 MB，新增磁盘占用由 623 MB 降至 409 MB；所需解析器仍保留。临时 systemd 单元 `knowledge-manager-install` 仍在执行安装，当前 apt 正在下载 `libllvm20`，应用尚未部署完成，也没有应用健康检查结果。

模型下载器已查明镜像 HTTP 403 的原因：镜像拒绝 Python 默认 User-Agent，项目专用 User-Agent 可用。相关下载器测试现为 11 项并已通过。新脚本 `scripts/check_server_ingestion.py` 可检查文本、TXT、Markdown、DOCX、XLSX、PDF 和旧 DOC 七类真实入库结果，但尚未在正式服务器运行。

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

1. 等待当前 apt 下载和临时单元 `knowledge-manager-install` 完成，检查最终状态与完整日志；若失败，按激活脚本的安全回滚状态排障，不能并发重启旧安装任务或直接手工启动旧代码。
2. 安装完成后核对 `knowledge-manager.service`、备份 timer、当前 release 链接和本机 `/api/health`；确认微信 service 在无凭据时保持关闭。
3. 在服务器运行尚未执行的 `scripts/check_server_ingestion.py` 七类真实入库验收和中文 OCR 检查，核对 Vault 原件、Markdown、SQLite 和服务账户权限；随后做服务重启和虚拟机重启验收。
4. 手动创建并校验正式备份，在独立目录恢复并核对清单、原件及数据库；配置第二备份介质。
5. 等待服务器主模型续传完成，按固定大小和 SHA-256 校验后再发布到模型缓存；在这台无 AVX2 的实际 CPU 上验收中文媒体转写性能与兼容性。AI 和微信凭据齐全后再分别启用并做端到端验收。

微信入口待用户实际公众号/微信客服账号配置。Obsidian Vault 是服务器上的文件夹；完整客户端访问方案和备份介质仍需部署后验证，不能把微信页面入口视为已完成 Obsidian 同步。
