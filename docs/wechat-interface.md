


凭据只写入 Ubuntu 的 `/etc/knowledge-manager/server.env`，不要写入 Git、聊天或日志。需要填写的变量如下：

```text
WECHAT_ENABLED=false
WECHAT_CORP_ID=<客服所属企业的 CorpID>
WECHAT_SECRET=<客服应用 Secret>
WECHAT_CALLBACK_TOKEN=<微信客服回调 Token>
WECHAT_ENCODING_AES_KEY=<微信客服回调 EncodingAESKey>
WECHAT_ALLOWED_ACCOUNTS=<允许接收的 open_kfid，逗号分隔>
WECHAT_ALLOWED_SENDERS=<允许发送资料的用户标识，逗号分隔>
```

`WECHAT_ALLOWED_ACCOUNTS` 和 `WECHAT_ALLOWED_SENDERS` 必须明确填写，系统不会默认放行所有账号。配置完整后先保持 `WECHAT_ENABLED=false`，验证回调入口和 Cloudflare 路由，再切换为 `true`。


Cloudflare Tunnel 只允许下面这一条路由：

```text
https://<你的域名>/wechat/callback -> http://127.0.0.1:8766/wechat/callback
```

其他路径统一返回 404。Tunnel 的 token 只在 Ubuntu 本地安装时输入：

```sh
sudo sh /opt/knowledge-manager/current/scripts/install_cloudflared_tunnel.sh \
  --hostname '<你的域名>' \
```

安装脚本会把 token 写入 `/etc/knowledge-manager/cloudflared/token`，权限为 `0600`。安装后运行：

```sh
sudo /opt/knowledge-manager/current/.venv/bin/python \
  /opt/knowledge-manager/current/scripts/check_public_entry.py \
  --config /etc/knowledge-manager/cloudflared/config.yml \
  --hostname '<你的域名>'
```



- 回调服务只监听 `127.0.0.1:8766`。
- 主服务管理 API、Vault、SQLite 和 Syncthing GUI 不通过 Cloudflare 暴露。
