from setuptools import setup

setup(
    name='lhr_sim_fmu', version='0.0.0', packages=['lhr_sim_fmu'],
    data_files=[('share/ament_index/resource_index/packages', ['resource/lhr_sim_fmu']),
                ('share/lhr_sim_fmu', ['package.xml'])],
    install_requires=['setuptools'], tests_require=['pytest'], zip_safe=True,
    maintainer='LHR', maintainer_email='autonomy@lhre.org', license='MIT',
    description='BobLib FMI 2.0 co-simulation vehicle plant.',
    entry_points={'console_scripts': ['sim_node = lhr_sim_fmu.sim_node:main']},
)
