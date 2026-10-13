from setuptools import setup, find_packages
from glob import glob
setup(name='piper_sim_devices', version='0.2.0', packages=find_packages(),
 data_files=[('share/ament_index/resource_index/packages', ['resource/piper_sim_devices']),
 ('share/piper_sim_devices', ['package.xml']), ('share/piper_sim_devices/launch', glob('launch/*.launch.py'))],
 install_requires=['setuptools'], license='MIT', entry_points={'console_scripts': ['isaac_arm_driver = piper_sim_devices.arm_driver:main']})
