"""Display sensor Z-depth in the main viewport when D405_Depth is selected."""

import numpy as np
import omni.kit.app
import omni.ui as ui
from omni.kit.viewport.utility import get_active_viewport_window


class D405ViewportViews:
    def __init__(self, viewport, rgb_camera, depth_camera):
        self.viewport = viewport
        self.rgb_path = rgb_camera.GetPath()
        self.depth_path = depth_camera.GetPath()
        self._selected_path = None
        self._latest_frame = None
        self._provider = ui.DynamicTextureProvider('piper_d405_main_depth')
        window = get_active_viewport_window(usd_context_name=viewport.usd_context_name)
        if window is None:
            raise RuntimeError('No viewport window for D405 depth display')
        self._overlay = window.get_frame('piper_d405_depth')
        # Leave the native viewport's camera/render menus accessible at the top.
        with self._overlay:
            with ui.VStack():
                ui.Spacer(height=40)
                with ui.ZStack():
                    ui.Rectangle(style={'background_color': 0xff000000})
                    ui.ImageWithProvider(self._provider,
                        fill_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT)
                self._status = ui.Label('Waiting for depth...', height=22,
                    style={'background_color': 0xff000000, 'color': 0xffffffff})
        self._overlay.visible = False
        self._subscription = viewport.subscribe_to_view_change(self._on_view_changed)
        # Identical matrices do not trigger a view-change event when only the
        # selected path changes. Also observe the camera path on Kit updates.
        self._update_subscription = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._on_update, name='D405 RGB/depth camera selection')

    def _on_update(self, *_):
        if self.viewport.camera_path != self._selected_path:
            self._on_view_changed()

    def _on_view_changed(self, *_):
        self._selected_path = self.viewport.camera_path
        self._overlay.visible = self._selected_path == self.depth_path
        if self._overlay.visible and self._latest_frame is not None:
            self.update_frame(self._latest_frame)

    def update_frame(self, frame):
        self._latest_frame = frame
        if not self._overlay.visible:
            return
        depth = frame['depth_m']
        valid = frame['valid_depth']
        scaled = np.zeros_like(depth)
        scaled[valid] = np.clip((depth[valid]-.05)/.55, 0, 1)
        colors = np.stack([1-scaled, 1-np.abs(2*scaled-1), scaled], axis=-1)
        colors = (255*colors).astype(np.uint8)
        colors[~valid] = 0
        rgba = np.concatenate([colors, np.full((*depth.shape, 1), 255, np.uint8)], axis=-1)
        self._provider.set_data_array(rgba.reshape(-1), [depth.shape[1], depth.shape[0]], strict=True)
        values = depth[valid]
        limits = f'visible {values.min():.3f}-{values.max():.3f} m' if values.size else 'no visible depth'
        self._status.text = f'Depth Z | red near / blue far: 0.05-0.60 m | {limits}'

    def close(self):
        self._subscription = None
        self._update_subscription = None
        self._overlay.visible = False
        self._overlay.clear()
