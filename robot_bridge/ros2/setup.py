from glob import glob

from setuptools import setup

setup(
    name="materialbrain_ros2",
    version="0.2.0",
    packages=["materialbrain_ros2"],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/materialbrain_ros2"]),
        ("share/materialbrain_ros2", ["package.xml", "LICENSE"]),
        ("share/materialbrain_ros2/launch", glob("launch/*.py")),
        ("share/materialbrain_ros2/config", glob("config/*")),
        ("share/materialbrain_ros2/urdf", glob("urdf/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="MaterialBrain maintainers",
    maintainer_email="maintainers@materialbrain.local",
    description="ROS Jazzy Nav2 laboratory simulation",
    license="MIT",
    entry_points={
        "console_scripts": [
            "sim_base = materialbrain_ros2.sim_base:main",
            "mission_bridge = materialbrain_ros2.mission_bridge:main",
            "export_map = materialbrain_ros2.export_map:main",
        ]
    },
)
