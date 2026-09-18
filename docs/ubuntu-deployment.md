# Ubuntu 部署记录

最终平台由用户确认：原先提到 CentOS，实际允许使用现有 Hyper-V 的 Ubuntu。本文记录已观察到的事实，不把配置文件存在当作部署完成。

## 目标和初始状态

- Windows 宿主机：Tailscale `100.64.186.105`，`Administrator`；已通过专用 Ed25519 密钥登录。
- 虚拟机：`Ubuntu`，ID `9f11fb39-9762-47ab-851d-a63f38e0f71b`；第二代，4 GiB RAM，1 vCPU。
- 网络：Default Switch，MAC `00:15:5d:03:17:03`；安装前没有来宾 IP 或心跳。
- 安装镜像：宿主机现有 Ubuntu 24.04.4 live-server amd64 ISO。
- 启动故障：控制台显示镜像签名被拒绝；固件使用 `MicrosoftWindows` 模板。
- 在控制台确认未启动 OS 后停止虚拟机，仅只读挂载系统盘检查：`E:\Ubuntu\Ubuntu\Virtual Hard Disks\Ubuntu.vhdx` 为 RAW、无分区，虚拟容量约 127 GiB，文件占用 4 MiB。随后卸载只读挂载。这是允许在空盘安装的前置证据。

## 已执行的准备

1. `scripts/configure-hyperv-linux.ps1` 在虚拟机关闭时设置 Linux CA 安全启动模板，保留安全启动；保留系统盘，暂卸安装介质，先验证硬盘能否独立引导。结果为无法加载 OS。
2. `scripts/build_ubuntu_seed.py` 生成 NoCloud 公钥安装盘，用户名 `knowledgeadmin`，主机名 `knowledge-server`。禁用 SSH 密码登录，只安装管理公钥，私钥仍在管理电脑；专用管理员通过 sudo 管理部署。
3. 校验本地与远端安装配置 ISO 的 SHA-256 一致；重新挂载 Ubuntu 安装镜像及新配置盘，原来的 `cidata-seed-v2.iso` 不使用。
4. 启动镜像后，实际控制台进入 Subiquity 自动安装配置，读取到了网络、文件系统、身份和 SSH 配置。确认对象是上述 RAW 空盘后，继续自动安装。配置要求安装结束关机。

**当前：curtin 主安装阶段已完成，OpenSSH 已安装到目标系统；安装器正在运行最后的安全更新。尚未验证最终关机及系统盘独立启动。** 安装环境地址为 `172.27.65.219`，可经宿主机转发公钥登录。2026-09-19 观察到原来的内核下载进程正常结束，`curtin: Installation finished`，随后进入 `run_unattended_upgrades`。期间没有重新安装或强行重启。

硬件实查：Intel i5-3320M，宿主机约 12GB 内存，目标磁盘 124GiB 可用容量、剩余约 116GiB；VM 当前 1 vCPU，动态分配约 2.3GiB（启动配置 4GiB）。本地大模型必须按该资源实测，不能直接使用默认 7B 配置。管理电脑已校验的语音模型仅部分传到目标 `/var/tmp/knowledge-manager-provision/model`，不可视为可用模型。大文件复制期间管理连接响应变慢，已暂停传输；后续需完整校验后再部署模型。

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

1. 观察安装结束/关机，卸载两张安装介质，设置系统盘优先，再启动并检查 IP。
2. 经宿主机 SSH 转发连入来宾，验证 `/etc/os-release`、磁盘挂载、网络、sudo 和专用密钥登录。
3. 分离程序、数据和模型目录，安装解析依赖，创建受限应用服务账户及 systemd 服务。
4. 在服务器真实运行所有格式入库、中文媒体和 OCR、真实 AI、微信收发测试。
5. 配置服务器数据备份；在独立目录恢复并核对清单、原件及数据库，再做重启验收。

微信入口待用户实际公众号/微信客服账号配置。Obsidian Vault 是服务器上的文件夹；完整客户端访问方案和备份介质仍需部署后验证，不能把微信页面入口视为已完成 Obsidian 同步。
