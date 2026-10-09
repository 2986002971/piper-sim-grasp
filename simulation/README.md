# 可选离线抓取工具

这些工具用于合成RGB-D采集、拟合回放和误差评估，不是串流服务的依赖。
采集实验使用Isaac Sim6.0；本次6.1串流验收没有重新验证RGB-D采集。

## 依赖与入口

采集需要Isaac、RTX GPU、Vulkan、NumPy、PyYAML、外部瓶体USD和毫米夹爪网格。
拟合需要NumPy、OpenCV及外部 `nankai_robocup_grasp_geometry` 包，通过 `GRASP_GEOMETRY_ROOT` 或 `PYTHONPATH` 提供。

| 入口 | 用途 |
|---|---|
| `generate_samples.py` | 静态视角或 `--continuous` 连续接近序列采集 |
| `fit_samples.py` | 图像带基线回放；提供共用Depth/CameraInfo数据适配 |
| `fit_anchored_samples.py` | 静态视角桌面锚定估计与基线对照 |
| `fit_sequence.py` | 连续帧估计、深度噪声/空洞/先验偏差与缺深度回放 |

示例标定和采集参数在[sim.example.yaml](../config/sim.example.yaml)，需替换为实际设备和目标模型。
采集明确提供 `--gripper-mesh-dir` 或 `PIPER_GRIPPER_MESH_DIR`；输出目录在源码仓库外。

```bash
export OMNI_KIT_ACCEPT_EULA=YES
export VK_ICD_FILENAMES=/path/to/nvidia-vulkan-icd.json
export TMPDIR=/path/to/large-temp-directory
export GRASP_GEOMETRY_ROOT=/path/to/external/grasp-geometry-package
python simulation/generate_samples.py --continuous \
  --config config/sim.example.yaml \
  --bottle-usd /path/to/bottle.usd \
  --gripper-mesh-dir /path/to/gripper-meshes \
  --output-dir /path/outside/repo/grasp-sequence
python simulation/fit_sequence.py \
  '/path/outside/repo/grasp-sequence/sample_*.npz' \
  --output /path/outside/repo/grasp-sequence/report.json \
  --drop-frames 35:50
```

## 历史合成结果（2026-10-06）

Isaac Sim6.0 / RTX5090以D405640×480标定和100mm夹爪采集90帧，按30Hz配置路径时间，
真实渲染耗时15.31秒，吞吐约5.9帧/秒。瓶体静止，相机从0.45m直线接近到0.18m，姿态固定。
抓取点真值为桌面支撑轴交点加115mm，瓶高230.2mm，半径先验27.5mm；真值和语义掩膜不输入估计器。

| 场景 | VALID / HELD | 水平误差P95 | 轴向绝对误差P95 | 水平抖动RMS |
|---|---:|---:|---:|---:|
| 清洁90帧 | 90 / 0 | 0.96mm | 0.60mm | 0.084mm |
| 0.6mm深度噪声、8%空洞、半径+1mm、初值+10mm、16帧缺深度 | 74 / 16 | 0.80mm | 0.67mm | 0.128mm |
| 同类噪声/空洞、半径−1mm、初值−10mm | 90 / 0 | 1.99mm | 0.67mm | 0.142mm |

缺深度期间保留最后可信目标及原源时间，恢复后回到VALID。独立静态72视角全部拟合，
水平误差P95为1.40mm，轴向误差P95为0.78mm；旧图像带中心轴向误差P95为21.76mm。
连续序列的旧图像带轴向误差P95约15.7–15.9mm。

这些指标只覆盖指定合成场景，不代表D405双目误差、反光、手眼误差、目标扰动或任意条件下的非线性收敛。
协方差仅为二维几何模型不确定度，不能直接作为生产中断门槛。
