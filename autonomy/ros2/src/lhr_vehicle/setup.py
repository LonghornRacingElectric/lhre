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
        # Installed into share/ because that is where a package:// URI
        # resolves, which is how the viewer fetches the car.
        (os.path.join('share', package_name, 'meshes'),
            glob('meshes/*.stl')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'vehicle_viz = lhr_vehicle.vehicle_viz_node:main',
        ],
    },
    maintainer='gray',
    maintainer_email='gray@todo.todo',
    description="Orion's physical parameters (vehicle.yaml), their loader, "
                'and a marker view of them.',
    license='MIT',
    tests_require=['pytest'],
)
