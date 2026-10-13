# AutoDL 串流网络约束

这套拓扑已经过 AutoDL 实测，是既定架构。目录整理、启动器合并和生命周期重构必须保留它；修改网络拓扑需另行与维护者讨论。

```text
浏览器 ── WS / 公网 TCP 转发 ──> Isaac 信令 :6006
浏览器 ── TURN/TCP 公网转发 ──> loopback :6008
                                  │ HAProxy，源地址绑定容器 IPv4
                                  └──> coturn 容器 IPv4:16010
                                           │ 容器内部 UDP
                                           └──> Isaac 容器 IPv4:47998
管理机 ── SSH ──> 服务器会话管理器及所属进程
```

公网只需要既有 TCP 转发和管理 SSH。Viewer 使用 `iceTransportPolicy=relay` 和 `turn:…?transport=tcp`；媒体仍经 TURN/TCP 公网入口。coturn 与 Isaac 位于同一容器网络命名空间，relay 地址及 Isaac 广告地址使用容器 IPv4，无 `external-ip`。UDP relay 分配端口保持 49152–49215。

HAProxy 的 `source <容器 IPv4>` 必须保留。此前平台直接从 loopback 连 coturn 时，Allocate 认证成功仍可能收集不到 Firefox relay candidate；绑定容器 IPv4 后的路线已有持续视频、重连和刷新证据。历史原因推断是浏览器校验 mapped 地址时拒绝 loopback；没有保留原始 Allocate 字段，不能把该推断说成逐字段证实。不要因看似多一跳而删除 HAProxy，也不要仅因 SDK 默认值将既有 WS 改成 WSS。

SSH 负责管理会话及断开清理；既定 TURN 路线不使用 SSH 搬运媒体、STUN 改写或 ICE 密码接口。历史自制 relay 属于已退役方案。

容器中的 NVENC 枚举过滤器仍只在串流 Isaac 子进程加载。它处理宿主多 GPU 枚举与容器设备可见性差异，不改变网络。`runtime/streaming` 保存网络配置和过滤器，`runtime/session.py` 管理所属进程。公网映射由 AutoDL 提供，部署脚本不修改平台隧道。

每次网络相关变更须用独立 Viewer 验证实际解码帧持续增长、重连和刷新；端口连通、ICE connected 或服务就绪标志均不能单独替代媒体验收。
