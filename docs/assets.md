# 场景资产与制作

运行需要完整资产包，而非单个顶层 USD。包包括 USD 层、网格、贴图、用于基础设备的已验证 URDF、装配 URDF、场景参数和校验清单。Isaac 内置 MDL 模块由固定版本的安装环境提供。

## 从当前正确场景导出

在已有 Isaac 环境的服务器执行：

```bash
./piper --local --config /private/machine.json assets export \
  --scene /external/scenes/piper_7up_grasp_scene.usda \
  --robot-urdf /external/models/piper/robot.urdf \
  --assembly-urdf /external/assembly/piper_x_d405.urdf \
  --scene-metadata /external/scenes/piper_7up_grasp_scene.json \
  --output /external/bundles/7up-v1.tar.gz
```

导出检查完整 USD 依赖，只修改临时副本，将文件引用改为包内相对路径。输入场景中的位置修正和原厂物理属性随包保留。URDF 必须已经解析 package:// 网格路径；导出不从未知 ROS 包猜测资产。

制作七喜场景时，可在导出前离线核对瓶子包围盒、桌面高度与参数清单，不启动 GPU：

```bash
bash runtime/isaac/python.sh /path/to/isaac-env checks/check_scene_contract.py \
  --scene /external/scenes/piper_7up_grasp_scene.usda \
  --manifest /external/scenes/piper_7up_grasp_scene.json \
  --output /external/reports/scene-contract.json
```

此检查面向当前七喜场景的固定 prim 和参数格式；场景真值只用于资产验收。

```bash
./piper --local --config /private/machine.json assets import /external/bundles/7up-v1.tar.gz
./piper --local --config /private/machine.json build
```

导入校验每个文件，拒绝归档越界和链接，在 assets/7up 下创建按内容区分的目录并更新 current.json。重复导入复用相同内容；损坏缓存会报错。切换资产后重新 build 生成本机配置，再重启设备。

场景配置固定在 config/profiles/7up.yaml。光学参数由资产的实际镜头属性发布；安装、增益、初始姿态、桌面碰撞和尺寸先验在 profile 中核对或配置。尺寸先验不包含供感知使用的目标真值位置。

## 维护资产

机器人源模型来源、来源提交与哈希见 models/sources.lock.json；装配生成工具保留在 models，说明见 models/README.md。日常部署使用完成的资产包，不要求重新下载支架或进行 STEP 转换。

[物品库](../models/objects/README.md)和[家电库](../models/appliances/README.md)提供独立的收集与物理制作工具，不是基础设备部署依赖。第三方模型保留来源和许可，公开再分发前按每项资产许可处理。

七喜瓶子当前的质量、惯量与接触验收仍未完成。原厂机器人动力学已存在于输入场景；不要用通用默认增益覆盖它。
