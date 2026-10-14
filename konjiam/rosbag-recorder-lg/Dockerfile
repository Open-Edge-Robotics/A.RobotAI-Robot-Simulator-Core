# rosbag recorder 이미지
# 공식 ROS2 Humble 이미지에 CycloneDDS RMW만 추가
FROM ros:humble-ros-base

RUN apt-get update \
 && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
 && rm -rf /var/lib/apt/lists/*
