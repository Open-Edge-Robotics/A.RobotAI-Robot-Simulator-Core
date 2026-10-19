# ============================================================
#  rosbag 자동 기록·NAS 업로드 (LG전자, v1.3)
#  기록(recorder)과 NAS 업로드(uploader)를 하나의 이미지로 실행
# ============================================================

# rclone 실행 파일만 가져오기 위한 단계 (버전 고정)
FROM rclone/rclone:1.75.2 AS rclone

# ROS2 Humble 공식 이미지 + CycloneDDS
FROM ros:humble-ros-base

RUN apt-get update \
 && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
 && rm -rf /var/lib/apt/lists/*

# rclone 실행 파일 복사 (정적 빌드라 별도 의존성 없음)
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

# 스크립트를 이미지 안에 포함
COPY entrypoint.sh recorder.sh uploader.sh /opt/rosbag-recorder/
RUN chmod +x /opt/rosbag-recorder/*.sh

# 기본 설정값 (실행 시 환경 변수로 덮어쓸 수 있음)
ENV ROBOT_ORG=lg \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    RECORD_TOPICS="/scan /scan_unified /tf /cmd_vel" \
    ROTATE_SEC=300 \
    OVERLAP_SEC=5 \
    TZ=KST-9 \
    RCLONE_CONFIG_NAS_TYPE=smb

# 종료 시 SIGINT를 보내 기록 중인 bag을 정상 마감
STOPSIGNAL SIGINT

ENTRYPOINT ["/opt/rosbag-recorder/entrypoint.sh"]
