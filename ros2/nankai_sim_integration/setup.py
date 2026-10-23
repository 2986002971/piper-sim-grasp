from setuptools import setup, find_packages
from glob import glob
setup(name='nankai_sim_integration', version='0.2.0', packages=find_packages(),
 data_files=[('share/ament_index/resource_index/packages', ['resource/nankai_sim_integration']),
 ('share/nankai_sim_integration', ['package.xml']), ('share/nankai_sim_integration/launch', glob('launch/*.launch.py'))],
 install_requires=['setuptools'], license='MIT', entry_points={'console_scripts': [
 'experiment = nankai_sim_integration.experiment:main',
 'pick_business = nankai_sim_integration.pick_business:main']})
