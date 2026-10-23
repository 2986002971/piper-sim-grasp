# Piper 仿真 ROS 设备

C++ 仿真驱动提供主项目 TCP reference/state、MoveGripper Action、实测 joint_states/gripper_state 和诊断。直接编译外部主项目控制核心，适配留在本仓库，不启动实机或 Mock 宏驱动。

设备 launch 同时拥有 RSP 与相机安装静态 TF，Isaac 进程由根目录 piper 管理。配置 main_project 时，显式 build 通过 NANKAI_MAIN_SOURCE 传入外部源码；基础设备模式只安装实测关节转发，不提供业务控制接口。

夹爪位置目标与驱动力上限通过标准 GripperCommand 送入 Isaac；有符号力来自 PhysX 实测关节 effort。接口、映射与时间语义见[架构说明](../../docs/architecture.md)。离线检查包含仿真映射测试及上游未修改的夹爪和运动核心测试。
