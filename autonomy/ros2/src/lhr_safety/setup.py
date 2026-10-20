from setuptools import setup

package_name = 'lhr_safety'

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
    maintainer='gray',
    maintainer_email='gray@todo.todo',
    description='Software half of the safety loop: fault checks, heartbeat, trip.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'safety_node = lhr_safety.safety_node:main',
        ],
    },
)
