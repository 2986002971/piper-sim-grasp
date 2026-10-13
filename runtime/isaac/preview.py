"""Viewport preview of the existing ROS render product."""
import time
import numpy as np
import omni.replicator.core as rep
from d405_viewport import D405ViewportViews


class Preview:
    def __init__(self, viewport, camera, depth_camera, product):
        self.views = D405ViewportViews(viewport, camera, depth_camera)
        self.last = 0.
        self.rgb = rep.AnnotatorRegistry.get_annotator('rgb')
        self.depth = rep.AnnotatorRegistry.get_annotator('distance_to_image_plane')
        self.product = product
        self.rgb.attach([product.path]); self.depth.attach([product.path])

    def update(self):
        if time.monotonic()-self.last < .2: return
        self.last = time.monotonic()
        rgb, depth = self.rgb.get_data(), self.depth.get_data()
        if not isinstance(rgb, np.ndarray) or not isinstance(depth, np.ndarray) or not depth.size: return
        frame = dict(rgb=rgb[..., :3], depth_m=depth,
                     valid_depth=np.isfinite(depth) & (depth > 0) & (depth < 2.))
        self.views.update_frame(frame)

    def close(self):
        self.rgb.detach([self.product.path]); self.depth.detach([self.product.path])
        self.views.close()
