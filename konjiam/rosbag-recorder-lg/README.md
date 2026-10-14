# rosbag 자동 기록·NAS 업로드

로봇이 켜지면 rosbag을 자동으로 기록하고, 기록이 끝난 파일을 NAS로 자동 업로드합니다.

- **recorder**: rosbag을 로봇 로컬 디스크(`/data/rosbag`)에 5분 단위로 기록
- **uploader**: 기록이 끝난 bag을 NAS로 전송하고 로컬에서 삭제. NAS 연결이 끊기면 로컬에 쌓아 두었다가 재연결 시 자동 전송

---

## 1. 설치 전 확인

로봇에 아래가 준비되어 있어야 합니다. ROS 기록 도구와 rclone은 컨테이너에 포함되어 있어 로봇에 따로 설치할 필요가 없습니다.

| 항목 | 조건 | 확인 명령 |
| --- | --- | --- |
| git | 설치 | `git --version` |
| Docker | 설치 | `docker --version` |
| Compose | v2 플러그인 (`docker compose`) | `docker compose version` |
| 빌드 도구 | buildx 플러그인 | `docker buildx version` |
| Docker 자동 시작 | `enabled` | `systemctl is-enabled docker` |
| 인터넷 | 설치(빌드) 시 GitHub·Docker Hub·ROS 패키지 저장소 접속 | |
| NAS 연결 | NAS의 445 포트 접속 | `timeout 3 bash -c '</dev/tcp/NAS주소/445' && echo OK` |
| ROS | ROS2 Humble, CycloneDDS | 로봇 ROS 터미널에서 `echo $ROS_DISTRO $RMW_IMPLEMENTATION` |
| 디스크 | 여유 수 GB 이상 | `df -h /` |

> `docker compose version`이 안 되고 `docker-compose --version`만 된다면 예전 Compose(v1)입니다. Compose v2 플러그인을 설치해야 합니다.

Docker 자동 시작이 `disabled`라면 아래로 켜 주세요. **부팅 시 자동 기록은 이 설정에 의존합니다.**

```bash
sudo systemctl enable --now docker
```

---

## 2. 설치

```bash
# 1) 저장소 받기
git clone https://github.com/Open-Edge-Robotics/A.RobotAI-Robot-Konjiam-Manual.git
cd A.RobotAI-Robot-Konjiam-Manual/rosbag-recorder-lg/opt/rosbag-recorder

# 2) 설정 파일 만들기
cp robot.env.example robot.env
chmod 600 robot.env
nano robot.env        # "입력 필요" 항목 작성 (3장 참고)

# 3) 빌드 및 실행 (최초 1회)
sudo docker compose up -d --build
```

- 이후 모든 `docker compose` 명령은 **`rosbag-recorder-lg/opt/rosbag-recorder` 폴더에서** 실행합니다.
- 최초 빌드는 ROS 기본 이미지를 받기 때문에 네트워크에 따라 수 분에서 십여 분 걸립니다.
- 한 번 실행하면 이후에는 **로봇을 재부팅해도 자동으로 다시 시작**됩니다.
- 이노그리드에서 계정 정보가 들어간 env 파일을 별도로 받은 경우, 2)에서 그 파일을 `robot.env`라는 이름으로 `opt/rosbag-recorder` 폴더에 넣으면 됩니다.
- 설치 후 저장소 폴더를 지우거나 옮기면 안 됩니다. 컨테이너가 이 폴더의 스크립트와 설정을 사용합니다.

---

## 3. robot.env 입력 항목

| 항목 | 설명 |
| --- | --- |
| `ROBOT_ID` | NAS 폴더 이름으로 쓰는 로봇 별칭. 영문 소문자·숫자·`-`·`_`만 사용 (예: `stretch01`) |
| `ROS_DOMAIN_ID` | **로봇의 값과 반드시 같아야 합니다.** 다르면 오류 없이 빈 bag만 쌓입니다. 로봇에서 `echo $ROS_DOMAIN_ID`로 확인, 비어 있으면 `0` |
| `RCLONE_CONFIG_NAS_HOST` | NAS IP만 입력 (`http://`, 포트 번호 없이) |
| `RCLONE_CONFIG_NAS_USER` | NAS 업로드 계정 |
| `RCLONE_CONFIG_NAS_PASS` | NAS 비밀번호 **변환값** (아래 참고) |

그 밖의 항목(토픽 목록, bag 길이 등)은 기본값으로 두면 됩니다. 각 항목 설명은 `robot.env.example`의 주석을 참고하세요.

**NAS 비밀번호 변환**

rclone은 비밀번호를 그대로 쓰지 않고 변환한 값을 사용합니다.

```bash
sudo docker run --rm -i rclone/rclone:1.75.2 obscure -
```

실행 후 비밀번호를 입력하고 Enter를 누르면 변환값이 나옵니다(안 나오면 `Ctrl + D`). 변환할 때마다 결과가 달라지지만 모두 같은 비밀번호이며 어느 값을 써도 됩니다.

> 변환값은 원래 비밀번호로 되돌릴 수 있어 암호화가 아닙니다. `robot.env`는 `chmod 600`으로 보호하고, **공개 저장소이므로 절대 git에 올리지 마세요**(`../../.gitignore`에 등록되어 있음).

**설정 값이 비어 있으면**

`<...>`가 남아 있거나 `ROS_DOMAIN_ID`가 숫자가 아니면, 컨테이너가 기록·업로드를 하지 않고 로그에 `[설정 오류]`를 남긴 채 대기합니다.

---

## 4. 동작 확인

`opt/rosbag-recorder` 폴더에서 실행합니다.

```bash
sudo docker compose ps                                  # recorder, uploader 모두 running
sudo docker compose logs --tail 20 recorder             # "Recording..." 확인
sudo docker compose logs --tail 20 uploader             # 업로드 로그
ls /data/rosbag/.recording                              # 기록 중인 bag 폴더
```

| 위치 | 의미 |
| --- | --- |
| `/data/rosbag/.recording/` | 기록 중인 bag |
| `/data/rosbag/` 바로 아래 폴더 | 기록이 끝나 NAS 전송을 기다리는 bag |
| `/data/rosbag/.broken/` | 열리지 않는 bag (전송하지 않음, 수동 확인용) |

NAS에는 `robot/<기관>/<로봇ID>/<날짜>/<기록시작시각>/` 형태로 저장됩니다.

---

## 5. 운영

`opt/rosbag-recorder` 폴더에서 실행합니다.

| 작업 | 명령 |
| --- | --- |
| 설정 변경 반영 | `sudo docker compose up -d` |
| 새 버전으로 업데이트 | `git pull && sudo docker compose up -d --build` |
| 중지 | `sudo docker compose stop` |
| 다시 시작 | `sudo docker compose up -d` |
| 완전히 제거 | `sudo docker compose down` |

- **robot.env를 고친 뒤에는 `restart`가 아니라 `up -d`를 실행하세요.** `restart`는 바뀐 설정을 다시 읽지 않습니다.
- `stop`이나 `down`으로 멈춘 경우, **재부팅해도 자동으로 시작되지 않습니다.** 다시 `up -d`를 실행해야 합니다.
- 중지하면 기록 중이던 bag은 정상 마감된 뒤 멈춥니다(최대 30초).
- `git pull`을 해도 `robot.env`는 git이 관리하지 않으므로 그대로 유지됩니다.

---

## 6. 문제 해결

| 증상 | 확인 |
| --- | --- |
| recorder 로그에 `[설정 오류]` | `robot.env`의 `ROBOT_ID`, `ROS_DOMAIN_ID` 확인 후 `up -d` |
| uploader 로그에 `[설정 오류]` | `robot.env`의 NAS 항목 확인 후 `up -d` |
| bag은 생기는데 메시지가 0개 | `ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION`이 로봇과 같은지 확인 |
| uploader 로그에 `connection refused` | NAS 주소·네트워크 확인. 연결되면 쌓인 bag이 자동 전송됨 |
| uploader 로그에 인증 오류 | NAS 계정·비밀번호 변환값 확인 |
| 재부팅 후 컨테이너가 안 뜸 | `systemctl is-enabled docker`가 `enabled`인지, 직전에 `stop`/`down`으로 멈추지 않았는지 확인 |
| `env file ... robot.env not found` | `opt/rosbag-recorder`에서 `cp robot.env.example robot.env` 후 입력 |
| `no configuration file provided` | `opt/rosbag-recorder` 폴더에서 실행했는지 확인 |

---

## 7. 파일 구성

```
rosbag-recorder-lg/
  ├─ Dockerfile                       ← recorder 이미지 (공식 ROS2 Humble + CycloneDDS)
  ├─ README.md                        ← 이 문서
  ├─ .gitignore                       ← robot.env, 이미지 파일 업로드 방지
  ├─ .gitattributes                   ← 스크립트 줄바꿈(LF) 고정
  └─ opt/rosbag-recorder/
      ├─ docker-compose.yml           ← recorder(빌드), uploader(rclone 1.75.2) 실행 설정
      ├─ recorder.sh                  ← 설정 검사, 5분 단위 겹침 로테이션 기록, 비정상 종료 복구
      ├─ uploader.sh                  ← 설정 검사, NAS 전송 및 로컬 정리
      └─ robot.env.example            ← 설정 예시 (복사해서 robot.env로 사용)
```