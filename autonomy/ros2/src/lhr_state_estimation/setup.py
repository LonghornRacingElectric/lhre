from setuptools import setup

package_name = 'lhr_state_estimation'

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
    maintainer='koa',
    maintainer_email='ya7897@eid.utexas.edu',
    description='EKF vehicle state estimation from IMU and wheel speeds.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'ekf_node = lhr_state_estimation.ekf_node:main',
        ],
    },
)
