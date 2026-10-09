# Piper Sim Grasp

Isaac Sim 服务端、TURN/TCP 串流管理、Piper X + D405 模型准备及离线抓取实验。
项目自有代码采用 [MIT](LICENSE)；下载的第三方模型不属于项目 MIT 授权。

- [服务端部署](docs/deployment.md)：安装、模型、平台转发、预检、启停和日志。
- [模型与坐标约定](models/README.md)：固定来源、官方支架首次登录下载、离线缓存。
- [迁移与恢复](docs/migration.md)、[排障](docs/troubleshooting.md)。
- [技术调查](docs/history/streaming-investigation.md)、[延迟短测](docs/isaac-turn-latency-quick.md)、[验证范围](docs/verification.md)。
- [第三方声明](THIRD_PARTY_NOTICES.md)。

客户端已拆分为独立 `piper-stream-viewer` Git 仓库。已构建包只需要 Python ≥3.10 和普通浏览器；
TURN 视频及控制由浏览器直接传输，本机 Python 只提供页面和配置。服务端不需要客户端 checkout。

```bash
python3 server/manage.py preflight --config /path/outside/repo/server.json
python3 server/manage.py start --config /path/outside/repo/server.json
python3 server/manage.py status --config /path/outside/repo/server.json
python3 server/manage.py export-client --config /path/outside/repo/server.json --output /path/outside/repo/client.json
python3 server/manage.py stop --config /path/outside/repo/server.json
```

默认 TURN。`server/` 管理 Isaac、coturn、HAProxy；`models/` 下载和生成仓库外资产；
`legacy/` 保留隔离的 relay 回退；`simulation/` 是需要外部依赖的可选研究工具。
当前场景提供机械臂、100 mm 夹爪和 D405 几何/坐标框架，没有 D405 RGB-D 传感器输出。

当前信令是 WS，页面是本机 HTTP，媒体经平台 TCP/HAProxy/coturn 承载。
管理仍由 Linux 管理机的 SSH 连接拥有，远端 worker 有 75 分钟 watchdog。
统一 HTTPS/WSS 托管、容器持久 supervisor 和长期稳定性属于后续工作。
