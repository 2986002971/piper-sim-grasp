# 验证范围

验证日期：2026-10-09 / 10（Asia/Shanghai）。部署版本和命令见[部署指南](deployment.md)。

## 自动检查

服务端8项unittest通过，覆盖TURN独立配置、配置迁移、coturn peer限制、部分启动失败清理、
进程归属/EOF退出、下载及缓存校验、离线损坏/失败、缺少USD输出的拒绝行为。
Python语法检查和NVENC过滤器 `gcc -shared -fPIC -Wall -Wextra` 编译通过。
客户端标准库HTTP、必要ICE/WS适配和统计探针检查通过；Node22.20.0 / npm10.9.3构建通过。
固定Vite6.3.5仍有开发服务器漏洞审计报告，交付包不包含该开发服务器。

## 模型

固定提交的模型描述、网格和RealSense许可证从空数据目录下载并逐文件校验通过；缓存与离线复用成功。
URDF检查机械臂Link6/7/8、100mm夹爪、单位、网格路径和D405光学变换。
官方支架认证下载的ZIP/STEP哈希通过，STEP与原来源文件逐字节一致。

Isaac Sim6.1生成带支架USD：11层、300网格、无未解析引用，发布到缓存后再次检查光学变换与引用通过。
缺支架离线生成：11层、299网格、无未解析引用；外部完整场景：9层、300网格，验证通过。
STEP转换与外部毫米STL覆盖的URDF装配通过。
装配仅提供几何和坐标框架，没有D405 RGB-D输出、动力学或夹爪控制验收。

## 浏览器与生命周期

远端为RTX4090、Isaac Sim6.1.0.0 / Python3.12；客户端为Linux桌面、Python3.13.14、可见Firefox154.0和geckodriver。
TURN使用自动生成的Piper X+D405场景，relay使用外部完整场景：
两种模式均通过1920×1080连续视频120秒、主动断开、重连、刷新和正常停止清理。
人工检查前后截图，确认机械臂可见且可信键鼠操作产生远端视角响应。
停止后拥有进程、配置、runtime和PID state被释放，状态中的四项就绪标志均为false。

Windows、macOS、Chromium及其他浏览器未做本轮媒体验收；现有GPU环境被复用，未验证从空操作系统全量安装。
本机HTTP页面、WS信令、TCP媒体、Linux管理机SSH生命周期及75分钟watchdog仍适用。
历史[延迟短测](isaac-turn-latency-quick.md)不代表长期稳定性；统一HTTPS/WSS托管和无人值守持久服务仍待验证。
