# Nankai 主项目接入

启动外部主项目 nankai_robocup_manipulation 的预定位 Source 或 Pick，以及 YOLO。协调器只激活检测器、调用现有业务接口、转发取消并保存实际反馈和结果，不实现抓取策略或夹爪完成判据。

`experiment start` 保留预定位；`experiment start --task pick` 执行完整 Pick。实验进程与持续运行的仿真设备分开，结束实验保留设备。正常 Pick 成功后按主项目语义继续携带与夹持。

配置由远端 profile 和主项目当前源码模板生成。main_project 不固定版本、不自动同步；所有仿真适配留在本仓库。见[操作说明](../../docs/operation.md)。
