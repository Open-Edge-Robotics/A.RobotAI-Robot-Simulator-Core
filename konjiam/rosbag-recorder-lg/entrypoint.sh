#!/bin/bash
# 기록(recorder)과 NAS 업로드(uploader)를 한 컨테이너에서 함께 실행한다.
# - 종료 신호를 받으면 recorder가 기록 중인 bag을 정상 마감한 뒤 종료한다.
# - 둘 중 하나가 멈추면 다른 하나도 정리하고 종료한다(재시작 정책으로 재기동).

DIR=/opt/rosbag-recorder

"$DIR/uploader.sh" &
UP_PID=$!
"$DIR/recorder.sh" &
REC_PID=$!

stop_all() {
  # 백그라운드로 실행한 스크립트는 SIGINT를 받을 수 없으므로 SIGTERM으로 전달한다.
  # recorder.sh는 SIGTERM을 받으면 ros2 bag record에 SIGINT를 보내 bag을 정상 마감한다.
  kill -TERM "$REC_PID" 2>/dev/null
  wait "$REC_PID" 2>/dev/null
  kill -TERM "$UP_PID" 2>/dev/null
  wait "$UP_PID" 2>/dev/null
}

trap 'stop_all; exit 0' INT TERM

# 둘 중 하나라도 종료되면 나머지를 정리하고 비정상 종료로 끝낸다.
wait -n "$REC_PID" "$UP_PID"
echo "[entrypoint] recorder 또는 uploader가 종료되어 컨테이너를 재시작합니다."
stop_all
exit 1
