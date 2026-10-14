#!/bin/sh

# 설정 검사: NAS 정보나 ROBOT_ID가 미입력(<...>)이면 업로드하지 않고 대기
case "$RCLONE_CONFIG_NAS_HOST|$RCLONE_CONFIG_NAS_USER|$RCLONE_CONFIG_NAS_PASS|$ROBOT_ID" in
  *"<"*|*"||"*|"|"*|*"|")
    echo "[설정 오류] robot.env 의 RCLONE_CONFIG_NAS_HOST/USER/PASS, ROBOT_ID 를 확인하세요."
    trap 'exit 1' INT TERM
    sleep 3600 & wait $!
    exit 1
    ;;
esac

while true; do
  for d in /data/*/; do            # .recording, .broken 같은 숨김 폴더는 제외됨
    [ -d "$d" ] || continue
    name=$(basename "$d")
    day=$(echo "$name" | cut -c1-8)
    rclone move "$d" "nas:robot/${ROBOT_ORG}/${ROBOT_ID}/${day}/${name}" \
      --delete-empty-src-dirs --retries 3
    rmdir "$d" 2>/dev/null         # 전송 완료로 비었으면 폴더 삭제
  done
  sleep 30
done
