FROM ros:humble

# 환경 변수 설정
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONPATH=/pod_server
ENV ROS_DISTRO=humble

# 로케일 및 기본 패키지 설치
RUN apt update && apt install -y \
    locales \
    software-properties-common \
    python3-pip \
    python3.10 \
    && locale-gen en_US en_US.UTF-8 \
    && update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    && add-apt-repository universe \
    && rm -rf /var/lib/apt/lists/*

# ROS 2 패키지 설치 (rosbag2 storage 플러그인 포함)
RUN apt update && apt install -y \
    ros-humble-desktop \
    ros-dev-tools \
    ros-humble-rosbag2-storage-default-plugins \
    ros-humble-rosbag2 \
    && rm -rf /var/lib/apt/lists/*

# Python 요구사항 설치
COPY requirements.txt /pod_server/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /pod_server/requirements.txt

# 소스 코드 복사
COPY src /pod_server/src

# ROS 환경 설정 스크립트 생성
RUN echo '#!/bin/bash\n\
source /opt/ros/humble/setup.bash\n\
exec "$@"' > /ros_entrypoint.sh && \
chmod +x /ros_entrypoint.sh

# ⚡ bash 접속 시에도 ROS 환경 적용
RUN echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc

# 엔트리포인트 설정
ENTRYPOINT ["/ros_entrypoint.sh"]

# 애플리케이션 실행
CMD ["uvicorn", "pod_server.src.main:app", "--host", "0.0.0.0", "--port", "8002"]