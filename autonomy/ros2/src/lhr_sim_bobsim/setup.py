from setuptools import setup

setup(
    name='lhr_sim_bobsim', version='0.0.0', packages=['lhr_sim_bobsim'],
    data_files=[('share/ament_index/resource_index/packages', ['resource/lhr_sim_bobsim']),
                ('share/lhr_sim_bobsim', ['package.xml'])],
    install_requires=['setuptools'], tests_require=['pytest'], zip_safe=True,
    maintainer='LHR', maintainer_email='autonomy@lhre.org',
    description='BobSim 3 DOF ROS vehicle plant.', license='MIT',
    entry_points={'console_scripts': ['sim_node = lhr_sim_bobsim.sim_node:main']},
)
