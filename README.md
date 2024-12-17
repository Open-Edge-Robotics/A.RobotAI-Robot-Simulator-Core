# robot-simulator-back
자율행동체 시뮬레이터 플랫폼

- **Project Template**: [taking](https://github.com/taking/java-spring-base-structure)

---

## 소개

### 사전 요구 사항
- **Name**: robot-simulator-back
- **Language**: Python 3.10.12
- **Build System**: pip
- **Environment Management**: venv

### 패키지
#### requirements.txt
```
asyncpg==0.30.0
fastapi==0.115.5
kubernetes==31.0.0
pydantic==2.9.2
pydantic-settings==2.6.1
Pygments==2.18.0
python-dotenv==1.0.1
PyYAML==6.0.2
SQLAlchemy==2.0.36
sqlmodel==0.0.22
starlette==0.41.2
uvicorn==0.31.1
minio==7.2.10
asyncio
requests
```

### 환경 변수
```
# DB Url
DATABASE_URL=postgresql+asyncpg://agent_user:qwe1212qwe@db:5432/agent_db

# Minio
MINIO_URL=minio-service.robot.svc.cluster.local:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=qwe1212qwe1212
MINIO_BUCKET_NAME=rosbag-data

# Api
API_STR=/api

# Postgresql
POSTGRES_USER=agent_user
POSTGRES_PASSWORD=qwe1212qwe
POSTGRES_DB=robot_db
PGDATA=/var/lib/postgresql/pgdata
```

<br>

## 실행 가이드

**해당 프로젝트는 기본적으로 `Openstack 192.168.160.135 인스턴스`에 세팅된 Kubernetes 환경에서 실행할 수 있습니다. 가이드는 '192.168.160.135' 환경 기준으로 설명합니다.**

### 1. 저장소 Clone

```bash
git clone https://(private token)@github.com/inno-rnd-project/robot-simulator-back.git
```

### 2. 가상 환경 생성 및 활성화
명령줄(cmd)에서 루트 디렉토리로 이동한 후 다음 명령어를 실행합니다.
```bash
python -m venv <가상환경 이름>
```

```bash
source <가상환경 이름>/bin/activate
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt
```

### 4. Docker 로그인

참고: 현재 모든 컨테이너의 기반 이미지는 innoagent에서 가져오도록 설정되어 있습니다.  
도커 이미지 이름을 변경하는 방법은 `배포 서버 > 도커 이미지 변경`에 후술
```bash
docker login
```
> ID: innoagent  
> PW: qwe1212!Q

### 5. Docker 이미지 빌드 및 Docker Hub에 이미지 push

명령줄(cmd)에서 프로젝트 디렉토리로 이동한 후 Docker 이미지를 빌드합니다.
```bash
cd path/to/robot-simulator
docker build -t innoagent:robot-simulator-back:<tag>.
```

Docker Hub에 이미지를 push 합니다.
```bash
docker push innoagent/robot-simulator-back:<tag>
```

### 6. ssh 접속

```bash
ssh root@192.168.160.135
```
> PW: qwe1212!Q

### 7. 서버 구동



Docker Hub에서 이미지를 pull 합니다.
```bash
docker pull innoagent/robot-simulator-back:<tag>
```
#TODO yaml 재실행 과정 작성 필요





<br>

## API

호출할 수 있는 API는 `routes` 폴더에 정의되어 있습니다. 예를 들어, `http://192.168.160.135:30020/api/instance` 와 같은 형태로 호출할 수 있습니다.

```
.
├── routes
│   ├── instance.py
│   ├── rosbag.py
│   ├── simulation.py
│   └── template.py
```

<br>

## 배포 서버 (192.168.160.135)
### namespaces
`$ kubectl get namespaces -A`

<table>
    <tr>
        <th scope="col">이름</th>
        <th scope="col">설명</th>
    </tr>
    <tr>
        <td>default</td>
        <td>비어있음</td>
    </tr>
    <tr>
        <td>kube-node-lease</td>
        <td>(default) 비어있음</td>
    </tr>
    <tr>
        <td>kube-public</td>
        <td>(default) 비어있음</td>
    </tr>
    <tr>
        <td>kube-system</td>
        <td>(default) 쿠버네티스 시스템</td>
    </tr>
    <tr>
        <td>kubernetes-dashboard</td>
        <td>쿠버네티스 대시보드 관련 시스템</td>
    </tr>
    <tr>
        <td>mymonitoring</td>
        <td>Prometheus, Grafana</td>
    </tr>
    <tr>
        <td>robot</td>
        <td>minio, postgresql, API 서버 파드</td>ㄴ
    </tr>
    <tr>
        <td>simulation-&lt;simulationId&gt;</td>
        <td>
            생성되는 시뮬레이션의 네임스페이스. 인스턴스 파드들이 속해 있음<br>
            인스턴스 파드 네이밍 규칙: instance-&lt;simulationId&gt;-&lt;instanceId&gt;
        </td>
    </tr>
  </table>

### 도커 이미지 변경
TODO: 도커 이미지 변경하는 방법 작성