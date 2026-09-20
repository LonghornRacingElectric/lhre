from glob import glob
import os

from setuptools import setup

package_name = 'lhr_vehicle'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='gray',
    maintainer_email='gray@todo.todo',
    description="Orion's physical parameters (vehicle.yaml) and their loader.",
    license='MIT',
    tests_require=['pytest'],
)
