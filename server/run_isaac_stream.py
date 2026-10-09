#!/usr/bin/env python3
"""Launch the Piper X + D405 scene with Isaac Sim 6.1 WebRTC streaming."""

import os

from isaacsim.simulation_app import SimulationApp

isaac_env = os.environ["ISAAC_ENV"]
scene = os.environ["PIPER_SCENE_USD"]
stream_public_ip = os.environ["STREAM_PUBLIC_IP"]

app = SimulationApp(
    {
        "headless": True,
        "hide_ui": False,
        "active_gpu": 0,
        "physics_gpu": 0,
        "multi_gpu": False,
        "renderer": "RayTracedLighting",
        "width": 1920,
        "height": 1080,
        "open_usd": scene,
        "limit_cpu_threads": 16,
        "extra_args": [
            f"--/exts/omni.kit.livestream.app/primaryStream/publicIp={stream_public_ip}",
            "--/exts/omni.kit.livestream.app/primaryStream/signalPort=6006",
            "--/exts/omni.kit.livestream.app/primaryStream/streamPort=47998",
            "--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=false",
            "--/log/channels/omni.kit.livestream.streamsdk=verbose",
        ],
    },
    experience=(
        f"{isaac_env}/lib/python3.12/site-packages/"
        "isaacsim/apps/isaacsim.exp.full.streaming.kit"
    ),
)
print("Piper X + D405 stage loaded; Isaac Sim WebRTC stream running", flush=True)

# In headless streaming mode the Kit viewport can start with its default camera
# aimed away from the opened USD. Give the stage a few frames to finish loading,
# then frame its default prim so the streamed viewport starts on the robot.
for _ in range(60):
    app.update()

try:
    import math

    from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport
    from pxr import Usd, UsdGeom

    viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("No active Kit viewport is available")
    # Frame against the viewport's own USD context; the app default context may
    # differ from the context actually rendered by the livestream viewport.
    stage = viewport.stage
    if stage is None:
        raise RuntimeError("The active viewport has no USD stage")

    scene_path = os.path.realpath(scene)
    viewport_layer = stage.GetRootLayer().identifier
    if os.path.realpath(viewport_layer) != scene_path:
        print(
            f"Viewport stage is {viewport_layer!r}; opening requested scene in its context",
            flush=True,
        )
        if not viewport.usd_context.open_stage(scene_path):
            raise RuntimeError(f"Could not open scene in viewport context: {scene_path}")
        for _ in range(30):
            app.update()
        stage = viewport.stage

    # Some Isaac app setups expose /World as the stage default prim even when
    # the opened scene's robot remains a root-level prim. Prefer the known
    # assembly prim and only fall back to the stage default if it has bounds.
    target_paths = ["/piper_x_d405_assembly", "/World/piper_x_d405_assembly"]
    target_prim = next(
        (stage.GetPrimAtPath(path) for path in target_paths
         if stage.GetPrimAtPath(path).IsValid()),
        None,
    )
    if target_prim is None:
        default_prim = stage.GetDefaultPrim()
        if default_prim.IsValid():
            target_prim = default_prim
    if target_prim is None or not target_prim.IsValid():
        raise RuntimeError("USD stage has no valid Piper assembly prim to frame")

    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    world_range = bounds.ComputeWorldBound(target_prim).GetRange()
    lower, upper = world_range.GetMin(), world_range.GetMax()
    if world_range.IsEmpty() or not all(math.isfinite(v) for v in (*lower, *upper)):
        raise RuntimeError(f"Piper prim {target_prim.GetPath()} has no finite world bounds")
    print(
        "Camera framing target: "
        f"{target_prim.GetPath()} bounds="
        f"[{tuple(round(v, 4) for v in lower)} .. "
        f"{tuple(round(v, 4) for v in upper)}]",
        flush=True,
    )

    framed = frame_viewport_prims(viewport, prims=[target_prim.GetPath().pathString])
    print(f"Camera framing {'succeeded' if framed else 'failed'}", flush=True)
    for _ in range(10):
        app.update()
except Exception as exc:
    # Keep the stream available for diagnosis even if this Kit build does not
    # expose a viewport API in headless mode.
    print(f"Camera framing unavailable: {type(exc).__name__}: {exc}", flush=True)

print("PIPER_ISAAC_READY", flush=True)

while app.is_running():
    app.update()
app.close()
