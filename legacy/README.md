# Legacy relay 最小恢复

默认路线是 [TURN](../docs/deployment.md)。此目录只保留单会话 UDP→TLS/TCP relay 和隔离管理器；
STUN改写原因、NVENC A/B及其他网络路线的证据见 [调查记录](../docs/history/streaming-investigation.md)。

relay 媒体需要 Linux 管理/浏览器机、LAN UDP 47998、TLS证书/token和SSH本地转发。
TURN客户端日常使用不需要这些条件。此回退仅用于已知网络兼容问题，页面切换不会启动任何服务。

1. 先正常 stop TURN，断开旧浏览器，确认远端6006/6008/47998释放。
2. 服务端私有配置额外提供 `client_lan_ip`、`remote_relay`、`remote_cert`、`remote_key`、`remote_token`、`ca_cert`、`token_file`。
   `remote_relay` 指向远端本目录 `udp_tcp_tunnel.py`；证书/key/token均在仓库外。
   可用 `migrate_config.py --include-legacy` 保留已有恢复配置。`legacy_client` 配置包含本机16006信令、LAN媒体47998。
3. 如需首次创建凭据，在远端私有目录用 openssl 生成带正确 SAN 的证书、`secrets.token_hex(32)` 生成token并写0600文件，
   可信交付证书和token到管理/浏览器机；私钥留远端。沿用已有匹配证书/token可直接恢复。
4. 执行以下命令；此模式同时拥有 Isaac、远端relay、媒体SSH、本机relay和信令SSH。

```bash
python3 server/manage.py preflight --config /private/server.json --mode relay
python3 server/manage.py start --config /private/server.json --mode relay
python3 server/manage.py stop --config /private/server.json
```

独立 Viewer 配置中的 `relay` 应为：`signalingServer: 127.0.0.1`、`signalingPort: 16006`、
`mediaServer: 实际浏览器机LAN_IP`、`mediaPort: 47998`。配置存在时才显示切换入口。
用该机器的loopback页面端口5174连接 `?transport=relay`；旧ICE密码API只允许本机5173/5174页面。
页面依然是新独立Viewer，兼容适配提交ICE-lite密码到16009；relay修正STUN mapped地址/integrity/fingerprint。
同一源地址 UDP 的媒体转发不能跨机器随意共享。回退验证包括视频、控制、断开重连、刷新和停止清理。

故障时不要全局kill。`manage.py stop` 释放本轮归属进程；极端SSH故障仍由remote worker的75分钟watchdog兜底。
若需要原样旧部署，在外部备份目录按保存的旧入口恢复，勿与新栈同时占端口。
