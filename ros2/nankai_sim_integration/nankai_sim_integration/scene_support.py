"""Declare the measured fixed base/table mounting contact in W's existing scene."""
import time

import rclpy
from moveit_msgs.msg import AllowedCollisionEntry, PlanningScene, PlanningSceneComponents
from moveit_msgs.srv import GetPlanningScene


def allow_fixed_mount_contact(node, timeout=20.):
    client = node.create_client(GetPlanningScene, '/get_planning_scene')
    publisher = node.create_publisher(PlanningScene, '/planning_scene', 1)
    deadline = time.monotonic()+timeout

    def wait(predicate):
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError('W fixed mounting contact configuration')
            rclpy.spin_once(node, timeout_sec=.02)

    def read_scene():
        if time.monotonic() > deadline:
            raise TimeoutError('W fixed mounting contact configuration')
        request = GetPlanningScene.Request()
        request.components.components = (PlanningSceneComponents.ALLOWED_COLLISION_MATRIX |
                                         PlanningSceneComponents.WORLD_OBJECT_NAMES)
        future = client.call_async(request)
        wait(future.done)
        response = future.result()
        if response is None:
            raise RuntimeError('W planning scene read failed')
        return response.scene

    try:
        wait(client.service_is_ready)
        while True:
            current = read_scene()
            if 'table_top' in {obj.id for obj in current.world.collision_objects}:
                break
            rclpy.spin_once(node, timeout_sec=.05)
        matrix = current.allowed_collision_matrix
        if 'base_link' not in matrix.entry_names:
            raise RuntimeError('W collision matrix has no base_link')
        # Preserve the full existing matrix and its defaults; add one object
        # name if needed, then change precisely the symmetric mounting pair.
        if 'table_top' not in matrix.entry_names:
            defaults = dict(zip(matrix.default_entry_names, matrix.default_entry_values))
            values = [defaults.get(name, defaults.get('table_top', False))
                      for name in matrix.entry_names]
            for row, value in zip(matrix.entry_values, values):
                row.enabled.append(value)
            matrix.entry_names.append('table_top')
            matrix.entry_values.append(AllowedCollisionEntry(enabled=values+[False]))
        base, table = (matrix.entry_names.index(name) for name in ('base_link', 'table_top'))
        matrix.entry_values[base].enabled[table] = True
        matrix.entry_values[table].enabled[base] = True
        message = PlanningScene(is_diff=True, allowed_collision_matrix=matrix)
        wait(lambda: publisher.get_subscription_count() > 0)
        publisher.publish(message)
        while True:
            actual = read_scene().allowed_collision_matrix
            if all(name in actual.entry_names for name in ('base_link', 'table_top')):
                a, b = (actual.entry_names.index(name) for name in ('base_link', 'table_top'))
                if actual.entry_values[a].enabled[b] and actual.entry_values[b].enabled[a]:
                    return {'allowed_pair': ['base_link', 'table_top'], 'verified': True}
            rclpy.spin_once(node, timeout_sec=.02)
    finally:
        node.destroy_client(client)
        node.destroy_publisher(publisher)
