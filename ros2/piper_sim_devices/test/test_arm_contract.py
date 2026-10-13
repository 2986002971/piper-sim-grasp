from copy import deepcopy
from types import SimpleNamespace as Record

import pytest
from piper_sim_devices.arm_contract import ARM_JOINTS, feedback_indices, position_target

LIMITS = {name: (-1., 1.) for name in ARM_JOINTS}


def command():
    return Record(joint_names=list(reversed(ARM_JOINTS)), points=[Record(
        positions=[.6, .5, .4, .3, .2, .1], velocities=[], accelerations=[], effort=[])])


def test_reorders_by_joint_name():
    assert position_target(command(), LIMITS) == [.1, .2, .3, .4, .5, .6]


@pytest.mark.parametrize('case', ['queue', 'duplicate', 'unknown', 'missing', 'nan', 'limit', 'velocity'])
def test_rejects_incompatible_commands(case):
    c = command()
    if case == 'queue': c.points.append(deepcopy(c.points[0]))
    if case == 'duplicate': c.joint_names[-1] = c.joint_names[0]
    if case == 'unknown': c.joint_names[-1] = 'other'
    if case == 'missing': c.points[0].positions.pop()
    if case == 'nan': c.points[0].positions[0] = float('nan')
    if case == 'limit': c.points[0].positions[0] = 1.1
    if case == 'velocity': c.points[0].velocities = [0.] * 6
    with pytest.raises(ValueError): position_target(c, LIMITS)


def test_feedback_keeps_measured_fingers_and_ignores_other_joints():
    m = Record(name=['extra', 'joint8', 'joint7'] + list(reversed(ARM_JOINTS)),
               position=[9., -.025, .025, .6, .5, .4, .3, .2, .1])
    indices = feedback_indices(m)
    assert [m.position[i] for i in indices] == [.1, .2, .3, .4, .5, .6, .025, -.025]
