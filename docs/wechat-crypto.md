# 微信回调验签与解密基础模块

`backend.wechat.crypto` 提供离线回调验签、解密和受限 XML 解析。另有可由调用方显式创建的独立回调应用；这些代码没有默认启用公网服务，也不访问真实账号。

## 接口

```python
from backend.wechat import CallbackCryptoError, WeChatCallbackCrypto

crypto = WeChatCallbackCrypto(token, encoding_aes_key, receiver_id)
# GET 验证：先由 HTTP 框架解码 URL 查询参数，再把 echostr 原样传入。
plaintext = crypto.decrypt_challenge(echostr, msg_signature, timestamp, nonce)
# 返回 plaintext 本身，不包 XML、JSON 或引号，不附加换行。

# POST 通知：只把解密后的内容交给消息层。
fields = crypto.decrypt_xml(request_body, msg_signature, timestamp, nonce)
```

`decrypt(encrypted, signature, timestamp, nonce)` 返回经过验签和接收方校验的原始消息字节；`decrypt_challenge` 额外验证 UTF-8；`decrypt_xml` 从外层 XML 取出唯一的 `Encrypt`，验签解密后返回平面字段字典。`parse_callback_xml`、`extract_encrypted` 是单独的解析工具，**不提供身份验证**。不要相信外层未签名的 `ToUserName` 或其他字段。

默认明文上限为 64 KiB，`max_payload_bytes` 可设置为 1–1,048,576 字节。密文长度按明文上限、协议头、接收方长度及填充计算，在 Base64 解码前限制输入。XML 同时限制字节数和 64 个字段，拒绝 DTD、实体声明、外部实体、重复字段、嵌套元素、属性和混杂文本；嵌套业务消息需要另行定义明确结构。所有无效输入抛出 `CallbackCryptoError`，错误消息不包含 Token、AESKey 或原文。

时间戳参与签名校验，但本模块不决定时效窗口、不防重放。接入层仍须限制 HTTP 请求大小、处理 URL 解码、限制请求速率、持久化幂等去重、校验发送者白名单，并及时应答后异步处理任务。`receiver_id` 必须明确配置且非空，企业应用回调使用企业 ID；其他产品应按对应官方文档确认接收方含义。

## 独立 HTTP 回调应用

`backend.wechat.callback.create_callback_app(store, settings)` 返回只提供 `/wechat/callback` GET/POST 的 FastAPI 应用，不挂载管理 API、Swagger 或 OpenAPI 页面。调用方先初始化 `WeChatStore`，再显式提供 `CallbackSettings(corp_id, token, encoding_aes_key, allowed_accounts)`；账号名单为空时不能启用。

GET 验签解密后原样返回 challenge。POST 按流读取，最大 96 KiB，即使 `Content-Length` 虚报或缺失也受限；拒绝重复或缺失的签名查询参数。两种请求均要求签名时间戳与服务器时间相差不超过 10 分钟。POST 解密后，必须匹配企业 ID、`MsgType=event`、`Event=kf_msg_or_event`、名单内 `OpenKfId` 及非空同步 `Token`，然后调用 `store.notify` 持久化同步请求，事务成功后才返回 `success`。签名或事件无效返回 403、超长请求返回 413、SQLite 存储故障返回 503，不把写入失败当成接收成功。

相同通知可以重投，持久层保留同一客服账号的一条待同步状态；后续消息仍按 `open_kfid + msgid` 去重。该入口不调用 `kf/sync_msg` 或发送 API，也不代替后续同步层的发送者白名单检查。公网部署还需要 HTTPS、时钟同步、入口限速与真实账号配置。

## 协议依据与测试来源

2026-09-19 读取并核对企业微信官方 [加解密方案说明](https://developer.work.weixin.qq.com/document/path/90968)：

- `EncodingAESKey` 是 43 个 Base64 字符，补 `=` 后解码为 32 字节 AESKey。
- AES-256-CBC，IV 为 AESKey 前 16 字节，PKCS#7 填充到 **32 字节**的倍数；不是通常 AES 工具默认的 16 字节填充。
- 解密帧为 `16 字节随机数 + 4 字节网络序消息长度 + 消息 + ReceiveId`。校验填充、帧长度、消息长度及完整 ReceiveId。
- 签名是 Token、timestamp、nonce、加密字符串按字典序排序后拼接的 SHA-1 小写十六进制；校验使用 `hmac.compare_digest`，先验签再解密。
- 实际 AES 与 PKCS#7 使用 `cryptography`，安全 XML 使用 `defusedxml`，未自行实现 AES。

`tests/test_wechat_crypto.py` 固定保留官方示例的密文、签名和解密后字段，并使用独立 .NET `System.Security.Cryptography.Aes` 生成的静态 challenge、32 字节完整填充和损坏帧样本。合成样本使用随机前缀 `00 01 ... 0f`、显式四字节大端消息长度及手工 32 字节填充，再以 .NET CBC、`Padding=None` 加密。测试不调用生产代码生成有效密文或预期签名；其中出现的 Token、AESKey、企业 ID 都是公开文档样例，不能用于真实账号。

这些测试证明本地协议兼容性及拒绝异常输入的行为，**不证明真实微信账号权限、实际回调连通、收件或结果送达**。真实验收仍按 [微信接口选型与实际限制](wechat-interface.md) 执行。
