ARG ROS_BASE_IMAGE=public.ecr.aws/docker/library/ros:jazzy-ros-base
FROM ${ROS_BASE_IMAGE} AS runtime_base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-navigation2 ros-jazzy-nav2-bringup \
    python3-numpy python3-yaml python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*
ENV ROS_DOMAIN_ID=147 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
WORKDIR /opt/materialbrain

FROM runtime_base AS robotics
ARG GIT_COMMIT=unknown
ENV MATERIALBRAIN_BUILD_SHA=${GIT_COMMIT}
COPY backend/app/services/embodied_navigation/world.v3.json /opt/materialbrain/world.v3.json
COPY backend/app/spatial/__init__.py backend/app/spatial/geometry.py backend/app/spatial/snapshot.py backend/app/spatial/export.py /opt/materialbrain/backend/app/spatial/
COPY backend/app/services/embodied_navigation/__init__.py backend/app/services/embodied_navigation/geometry.py backend/app/services/embodied_navigation/planner.py backend/app/services/embodied_navigation/world.v3.json /opt/materialbrain/backend/app/services/embodied_navigation/
COPY robot_bridge/ros2 /opt/materialbrain/ros2
RUN PYTHONPATH=/opt/materialbrain/backend python3 -m app.spatial.export --output /opt/materialbrain/spatial_snapshot.json && \
    . /opt/ros/jazzy/setup.sh && cd /opt/materialbrain/ros2 && \
    colcon build --merge-install --packages-select materialbrain_ros2 && chmod +x scripts/entrypoint.sh
EXPOSE 8766
ENTRYPOINT ["/opt/materialbrain/ros2/scripts/entrypoint.sh"]
