# 物品资产验收记录

以下为特定机器和资产批次的历史记录；迁移后需重新运行检查。制作流程见 [README](README.md)。

## 2026-10-13 交付

2026-10-13 曾下载 **33 个源模型包、筛选 56 个模型**，原压缩包合计约 236 MiB。
模型均为类别样本，尚未确认与比赛现场具体物品的品牌、尺寸或颜色一致。
本机已按用户要求删除原始下载包和旧试验批次，保留已解包源模型、当前完整物理资产及制作缓存。

全部 **56 套首版物理 USD** 已生成，批次 `88638a3b29697cf0`：
包含贴图、7,307 个凸碰撞体，以及质量、重心、完整/主惯量、主轴姿态和接触/求解设置。
28 个质量为 YCB 论文参考值，另外 28 个为工程假设；惯量与接触参数尚未实物标定。

验收记录：56 个资产文件哈希和 USD 结构检查通过；实际远端 Isaac Sim `6.1.0.0` 中，
56 个刚体的 8 秒落地/静止检查和 8 个开放杯碗的小球进入检查全部通过，步长 `1/240 s`。
实际烘焙凸体的最终最低高度约为 −0.027～0.001 mm；原几何与烘焙凸体的最大半空间偏差下界约为 4.28 mm。
13 项收集/物理自动测试通过。完整报告保留逐项结果和烘焙误差；验收不包含机器人抓放或实物标定。

本机交付目录：
`/srv/dev-workspace/model-data/robocup-at-home-2026/objects-v1/physics/88638a3b29697cf0/`。
远端同批目录：
`/root/autodl-tmp/robocup-at-home-2026/objects-v1/physics/88638a3b29697cf0/`。
每个 `<object-id>/object.usdc` 可单独引用；随物品目录一起移动 `textures/`。
本机 `physics_summary.csv` 汇总参数与运行结果，`physics_inventory.json` 指向本机路径，
`remote_physics_inventory.json` 保留远端原始清单；`dynamics_isaac.json` 为实际运行证据。
本次独立制作环境使用 OpenUSD `25.11`；Isaac 运行环境保持原有安装。


## 历史验证环境

用户指定的远端为 `ssh -p 30529 root@connect.bjb1.seetacloud.com`，
已确认 Isaac Sim `6.1.0.0`、RTX 4090。独立制作目录：
`/root/autodl-tmp/robocup-at-home-2026/`，下设 `code/models/objects/`、`tools-env/` 和 `objects-v1/`。
Isaac 验证使用 `/root/autodl-tmp/isaacsim-6.1-env/bin/python`。
