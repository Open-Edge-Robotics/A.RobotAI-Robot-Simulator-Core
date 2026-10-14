#!/bin/bash
source /opt/ros/humble/setup.bash

# 설정 검사: 미입력(<...>)이거나 ROS_DOMAIN_ID가 숫자가 아니면 기록하지 않고 대기
if grep -q '<' <<< "$ROBOT_ID$ROS_DOMAIN_ID" || ! [[ "$ROS_DOMAIN_ID" =~ ^[0-9]+$ ]]; then
  echo "[설정 오류] robot.env 의 ROBOT_ID, ROS_DOMAIN_ID 를 확인하세요."
  trap 'exit 1' INT TERM          # PID 1은 기본적으로 종료 신호를 무시하므로 직접 처리
  sleep 3600 & wait $!            # 신호를 받을 수 있게 대기 (1시간마다 재확인)
  exit 1
fi

mkdir -p /data/.recording /data/.broken
OVERLAP_SEC=${OVERLAP_SEC:-5}

finalize() {   # $1: bag 디렉터리. 검증 후 /data로 이동
  local dir="$1"
  [ -d "$dir" ] || return
  [ -f "$dir/metadata.yaml" ] || ros2 bag reindex "$dir" -s sqlite3
  if ros2 bag info "$dir" > /dev/null 2>&1; then
    mv "$dir" /data/
  else
    mv "$dir" /data/.broken/
  fi
}

start_rec() {  # 새 bag 기록 시작
  NEW_DIR=/data/.recording/$(date +%Y%m%d-%H%M%S)
  ros2 bag record ${RECORD_TOPICS} --storage sqlite3 -o "$NEW_DIR" &
  NEW_PID=$!
}

stop_rec() {   # $1: pid, $2: bag 디렉터리
  kill -INT "$1" 2>/dev/null
  wait "$1" 2>/dev/null
  finalize "$2"
}

trap 'stop_rec "$PID" "$DIR";
      [ "$NEW_PID" != "$PID" ] && stop_rec "$NEW_PID" "$NEW_DIR";
      exit 0' INT TERM

# 1. 이전에 비정상 종료로 남은 bag 처리
for d in /data/.recording/*/; do finalize "${d%/}"; done

# 2. 겹침 로테이션
start_rec; PID=$NEW_PID; DIR=$NEW_DIR
while true; do
  for ((i = 0; i < ROTATE_SEC - OVERLAP_SEC; i++)); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  start_rec                        # 다음 bag 먼저 시작
  sleep "$OVERLAP_SEC"             # 토픽 구독 대기
  stop_rec "$PID" "$DIR"           # 이전 bag 마감
  PID=$NEW_PID; DIR=$NEW_DIR
done
