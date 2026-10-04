from setuptools import setup

package_name = 'lhr_lidar_sim'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Tanush Chauhan',
    maintainer_email='tanushchauhan07@gmail.com',
    description='Synthetic Livox Mid-360 point cloud for FSAE driverless.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'lidar_sim = lhr_lidar_sim.lidar_sim_node:main',
        ],
    },
)
