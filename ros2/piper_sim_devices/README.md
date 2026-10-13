# Piper 仿真 ROS 设备

标准 ROS 消息适配包，不依赖主项目。接收六轴位置 JointTrajectory，转换成 Isaac 关节目标；反馈来自物理仿真。设备 launch 只启动机械臂转接和相机静态安装 TF，Isaac 由根目录 piper 管理器拥有。RSP 属于实验或独立验收。

部署、启动和接口见根目录 README 与 docs/architecture.md。
