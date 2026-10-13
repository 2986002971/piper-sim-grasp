# Third-party sources and notices

The root MIT license covers project-authored code and documentation. It does not relicense downloaded models, CAD, NVIDIA binaries, SDKs or generated scenes containing third-party geometry.
Upstream assets and raw/private data are stored outside this source checkout. Deployment asset bundles retain the applicable upstream terms.

| Component | Source and applicability |
|---|---|
| Piper X URDF/meshes and official USD | [AgileX](https://github.com/agilexrobotics/piper_isaac_sim), commit `8e1f88fdb7afca49c40e9a0c1c01cc588e86f0d2`. Snapshot metadata declares `TODO: License declaration`; no asset redistribution license was recorded. Model download is for local deployment; the source repository contains no upstream meshes/USD; private deployment asset bundles retain these provenance and redistribution limitations. |
| RealSense D405 description/mesh | AgileX copy of [RealSense ROS](https://github.com/realsenseai/realsense-ros), source commit `bafc21080c5c8e259dadbb309797949aee0dd950`. Apache-2.0; importer retains expanded source notices, and the pinned root LICENSE is downloaded with the external assembly. Hashes and upstream paths are in `models/sources.lock.json`. |
| D405 bracket CAD | [AgileX official model page](https://agilexsupport.yuque.com/staff-hso6mo/tetraz/km95una15ctk4b17?singleDoc), authenticated official D405 attachment. ZIP/STEP checksums verified against preserved original STEP. Redistribution terms were not recorded; cache and converted geometry stay outside Git. |
| Isaac Sim, Omniverse, Livestream/NVENC runtime | Installed separately under applicable NVIDIA terms; no NVIDIA runtime libraries are bundled here. Generated USD includes third-party geometry and remains an external deployment artifact. |
| NVENC ioctl definitions | `runtime/streaming/nvenc_ioctl_filter.c` reproduces interface layouts/constants from [NVIDIA open-gpu-kernel-modules](https://github.com/NVIDIA/open-gpu-kernel-modules). Upstream kernel code is dual MIT/GPL-2.0; retained interface definitions use the MIT option, with NVIDIA notice in `third_party/NVIDIA-MIT-LICENSE.txt`. The project interposer implementation is MIT. |
| STEP converter dependency | `cadquery-ocp`/OpenCascade and VTK installed separately. Their own package licenses apply; none of these wheels or libraries ship in this repository. |
| Object and appliance models | Sources, attribution and license records are maintained in [the object catalog](models/objects/catalog.json) and [the appliance catalog](models/appliances/catalog.json). Their geometry, textures and generated assets remain external; these models are not relicensed under the root MIT license. |

The independent `piper-stream-viewer` repository carries the NVIDIA streaming SDK and dependency notices for its source/built client.
