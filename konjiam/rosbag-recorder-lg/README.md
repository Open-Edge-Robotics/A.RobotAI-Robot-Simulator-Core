# rosbag 자동 기록·NAS 업로드 (LG전자, v1.3)

로봇의 ROS2 토픽을 rosbag으로 자동 기록하고, 기록이 끝난 파일을 NAS로 자동 업로드합니다.
**기록과 업로드가 하나의 이미지**에 들어 있어, `docker build` → `docker run`만으로 동작합니다.

- **기록**: 5분 단위로 bag을 만들고, 전환 시 앞뒤를 5초 겹쳐 데이터 공백을 줄임
- **업로드**: 기록이 끝난 bag을 30초 주기로 NAS(SMB)에 전송하고 로컬에서 삭제. NAS 연결이 끊기면 쌓아 두었다가 재연결 시 자동 전송

---

## 1. 소스 위치

- 저장소: `https://github.com/Open-Edge-Robotics/A.RobotAI-Robot-Simulator-Core`
- 빌드 경로(build context): **`rosbag-recorder-lg/`** (이 README가 있는 폴더)
- Dockerfile: `rosbag-recorder-lg/Dockerfile`

```bash
git clone https://github.com/Open-Edge-Robotics/A.RobotAI-Robot-Simulator-Core.git
cd A.RobotAI-Robot-Simulator-Core/rosbag-recorder-lg
```

---

## 2. 빌드

```bash
docker build -t rosbag-recorder-lg .
```

- 빌드 시 인터넷 접속이 필요합니다 (Docker Hub의 `ros:humble-ros-base`, `rclone/rclone:1.75.2`, ROS 패키지 저장소).
- 이미지 구성: ROS2 Humble + CycloneDDS + rclone 1.75.2 + 실행 스크립트

---

## 3. 실행

```bash
cp robot.env.example robot.env      # 필수 항목 입력 (4장)

docker run -d --name rosbag-recorder \
  --network host \
  --ipc host \
  --restart unless-stopped \
  --stop-timeout 30 \
  --env-file robot.env \
  rosbag-recorder-lg
```

### 실행 조건

| 옵션 | 필요 여부 | 이유 |
| --- | --- | --- |
| `--network host` | **필수** | 로봇의 ROS2 토픽(DDS)을 수신하기 위해 |
| `--ipc host` | **권장** | 같은 호스트의 ROS2 노드와 공유 메모리 통신 시 필요 |
| 환경 변수 (4장 필수 항목) | **필수** | 로봇 ID, ROS_DOMAIN_ID, NAS 접속 정보 |
| `--stop-timeout 30` | **권장** | 종료 시 기록 중인 bag을 정상 마감할 시간 확보 (기본 10초는 부족할 수 있음) |
| 종료 신호 SIGINT | 이미지에 지정됨 | Dockerfile의 `STOPSIGNAL SIGINT`. 별도 지정 불필요 |
| `--restart unless-stopped` | **권장** | 로봇 재부팅·비정상 종료 시 자동 재시작 |
| `-v /data/rosbag:/data` | 선택 | 미전송 bag을 로봇 디스크에 보존 (5장 참고) |

---

## 4. 환경 변수

### 필수

| 변수 | 설명 |
| --- | --- |
| `ROBOT_ID` | NAS 폴더 이름으로 쓰는 로봇 별칭. 영문 소문자·숫자·`-`·`_` (예: `stretch01`) |
| `ROS_DOMAIN_ID` | **로봇과 반드시 같은 값.** 다르면 오류 없이 빈 bag만 기록됨. 로봇에서 `echo $ROS_DOMAIN_ID` 로 확인, 비어 있으면 `0` |
| `RCLONE_CONFIG_NAS_HOST` | NAS IP (포트 번호 없이) |
| `RCLONE_CONFIG_NAS_USER` | NAS 업로드 계정 |
| `RCLONE_CONFIG_NAS_PASS` | NAS 비밀번호 **변환값** (아래 참고) |

### 선택 (이미지 기본값)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `RECORD_TOPICS` | `/scan /scan_unified /tf /cmd_vel` | 기록할 토픽 (띄어쓰기 구분) |
| `ROTATE_SEC` | `300` | bag 하나의 길이(초) |
| `OVERLAP_SEC` | `5` | bag 전환 시 겹침 시간(초) |
| `RMW_IMPLEMENTATION` | `rmw_cyclonedds_cpp` | ROS 미들웨어 (로봇과 동일 권장) |
| `ROBOT_ORG` | `lg` | NAS 경로 첫 단계 |
| `TZ` | `KST-9` | 폴더 이름 시간대 (한국 시간) |
| `RCLONE_CONFIG_NAS_TYPE` | `smb` | NAS 접속 방식 |

### NAS 비밀번호 변환

```bash
docker run --rm -i rclone/rclone:1.75.2 obscure -
```

비밀번호를 입력하고 Enter를 누르면 변환값이 나옵니다(안 나오면 `Ctrl + D`). 변환할 때마다 결과가 달라지지만 모두 같은 비밀번호입니다.

### 주의

- `--env-file`은 **따옴표를 값에 그대로 포함**합니다. `RECORD_TOPICS="/scan /tf"`처럼 쓰면 따옴표까지 토픽 이름이 되어 기록되지 않으므로 따옴표 없이 씁니다.
- 필수 항목이 비어 있거나 `<...>`가 남아 있으면, 기록·업로드를 하지 않고 로그에 `[설정 오류]`를 남긴 채 대기합니다.
- 비밀번호 변환값은 원래 비밀번호로 되돌릴 수 있어 암호화가 아닙니다. `robot.env`는 권한을 제한(`chmod 600`)하고, **공개 저장소에 올리지 마세요**(`../../.gitignore`에 등록되어 있음).

---

## 5. 데이터 저장과 보존

| 경로 (컨테이너 내부) | 의미 |
| --- | --- |
| `/data/.recording/` | 기록 중인 bag |
| `/data/` 바로 아래 폴더 | 기록이 끝나 NAS 전송을 기다리는 bag |
| `/data/.broken/` | 열리지 않는 bag (전송하지 않음) |

NAS에는 `robot/<ROBOT_ORG>/<ROBOT_ID>/<날짜>/<기록시작시각>/` 형태로 저장됩니다.

**볼륨을 연결하지 않는 경우**

bag 하나가 끝나면 30초 안에 NAS로 전송되므로, 로봇에는 NAS 연결이 끊긴 동안의 분량만 잠시 남습니다.

| 상황 | 아직 전송되지 않은 bag |
| --- | --- |
| 컨테이너 재시작, 로봇 재부팅 | 유지됨 (같은 컨테이너가 다시 시작되므로) |
| 컨테이너 삭제 후 재생성 (업데이트·재빌드) | **사라짐** |

업데이트 전에는 NAS 연결 상태에서 전송이 끝났는지 확인한 뒤 컨테이너를 교체하세요. 더 실시간에 가깝게 하려면 `ROTATE_SEC`를 줄입니다(예: `60`, 파일 수는 늘어남).

---

## 6. 동작 확인

```bash
docker logs --tail 30 rosbag-recorder
docker exec rosbag-recorder ls /data/.recording /data
```

| 로그 | 의미 |
| --- | --- |
| `Recording...` | 기록 중 (정상) |
| `[설정 오류] ... ROBOT_ID, ROS_DOMAIN_ID` | 기록 설정 확인 필요 |
| `[설정 오류] ... RCLONE_CONFIG_NAS_*` | NAS 설정 확인 필요 |
| `connection refused` | NAS 연결 불가. 연결되면 쌓인 bag 자동 전송 |
| `[entrypoint] ... 재시작합니다` | 내부 프로세스 종료로 컨테이너 재시작 |

- 업로드 로그는 첫 bag이 끝나는 시점(`ROTATE_SEC`)부터 나타납니다. 그 전에는 업로드 로그가 없는 것이 정상입니다.
- bag이 생기는데 메시지가 0개라면 `ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION`이 로봇과 같은지 확인하세요.

---

## 7. 포팅 시 유의사항 (Dockerfile을 수정하는 경우)

로봇 환경에 맞게 Dockerfile을 수정할 때 아래는 유지해야 합니다.

- `entrypoint.sh`, `recorder.sh`, `uploader.sh`를 이미지에 포함하고 `entrypoint.sh`를 시작 명령(ENTRYPOINT)으로 사용
- 종료 신호는 **SIGINT 또는 SIGTERM** (둘 다 entrypoint가 처리하여 bag을 정상 마감). `SIGKILL`로 바로 종료하면 기록 중인 bag이 마감되지 않음
- 종료 대기 시간 **30초 이상**
- `ros-humble-rmw-cyclonedds-cpp` 설치 (LG 로봇이 CycloneDDS 사용)
- rclone 1.60 이상 (SMB 지원). Ubuntu 22.04 apt의 rclone은 버전이 낮아 SMB를 지원하지 않으므로, Dockerfile처럼 공식 rclone 이미지에서 실행 파일을 복사하는 방식을 권장
- 스크립트는 LF 줄바꿈 유지 (CRLF면 `$'\r': command not found` 오류)

---

## 8. 파일 구성

```
rosbag-recorder-lg/
  ├─ Dockerfile            ← 이미지 정의 (ROS2 Humble + CycloneDDS + rclone + 스크립트)
  ├─ entrypoint.sh         ← 기록·업로드 동시 실행, 종료 신호 전달
  ├─ recorder.sh           ← 설정 검사, 겹침 로테이션 기록, 비정상 종료 복구
  ├─ uploader.sh           ← 설정 검사, NAS 전송 및 로컬 정리
  ├─ robot.env.example     ← 환경 변수 예시 (복사해서 robot.env로 사용)
  ├─ docker-compose.yml    ← 로컬 테스트용 (선택, 로봇 sandbox에서는 미사용)
  ├─ README.md
  ├─ .gitignore            ← robot.env 업로드 방지
  └─ .gitattributes        ← 스크립트 LF 줄바꿈 고정
```

## 버전

| 버전 | 변경 내용 |
| --- | --- |
| 1.3 | 기록·업로드를 **하나의 이미지**로 통합. 스크립트를 이미지에 포함하고 시작 명령을 Dockerfile에 지정하여 compose 없이 `docker build` → `docker run`으로 동작. 기본 설정값을 이미지에 포함, 필수 환경 변수만 입력. 폴더 구조 단순화(opt/ 제거) |
| 1.2 | git 배포 방식 전환 (compose 기반) |
| 1.1 | recorder 설정 검사 추가 |
| 1.0 | 최초 오프라인 패키지 |
