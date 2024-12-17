 # Pod Server
- 자율행동체 플랫폼에서 인스턴스를 생성할 때 함께 생성되는 kubernetes pod의 템플릿         
- rosbag file의 실행/중지가 진행되며, 자율행동체 플랫폼에서 실행 상태체크 요청의 결과를 반환하는 API가 포함됨

<br>

## 소개

### 사전 요구 사항
- **Name**: pod-server
- **Language**: Python 3.10.12
- **Build System**: pip
- **Container**: Docker 27.3.1


<br>

##  사용 가이드

**해당 프로젝트는 Openstack 인스턴스에 세팅된 Ubuntu 22.04 환경에서 실행됩니다.**

### 1. 프로젝트 저장
로컬에 프로젝트 파일을 저장합니다. #TODO 수정 ?

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

참고: 현재 모든 컨테이너의 기반 이미지는 `innoagent`에서 가져오도록 설정되어 있습니다.  

```bash
docker login
```
> ID: innoagent  
> PW: qwe1212!Q

### 5. Docker 이미지 빌드 및 Docker Hub에 이미지 push

명령줄(cmd)에서 프로젝트 디렉토리로 이동한 후 Docker 이미지를 빌드합니다.
```bash
cd path/to/pod_server
docker build -t innoagent:pod_server:<tag> .
```

Docker Hub에 이미지를 push 합니다.
```bash
docker push innoagent/pod_server:<tag>
```

### 6. ssh 접속 및 Docker Hub에서 이미지 pull

```bash
ssh root@192.168.160.135
```
> PW: qwe1212!Q


Docker Hub에서 이미지를 pull 합니다.
```bash
docker pull innoagent/pod_server:<tag>
```



<br>

## API

호출할 수 있는 API는 `routes` 폴더에 정의되어 있습니다.           
이를 자율행동체 플랫폼에서 호출하여 사용되며, 실행/중지는 pod별 서버에서 진행됩니다.

| 메서드  | 엔드포인트          | 기능                   |
|------|----------------|----------------------|
| POST | /rosbag/play   | rosbag file 재생       |
| POST | /rosbag/stop   | rosbag file 정지       |
| GET  | /rosbag/status | rosbag file 실행 상태 조회 |



<br>

## Docker 이미지 변경하는 경우
### 1. Docker 이미지 빌드 및 Docker Hub에 push
[사용 가이드](#5-docker-이미지-빌드-및-docker-hub에-이미지-push) 내용과 동일하게 진행합니다.

### 2. ssh 접속 및 Docker Hub에서 이미지 pull
[사용 가이드](#6-ssh-접속-및-docker-hub에서-이미지-pull) 내용과 동일하게 진행합니다.

### 3. `pod-template.yaml` 수정
backend-server/src/pod-template.yaml 파일의 아래 내용을 변경할 이미지명으로 수정합니다.
```yaml
spec:
  containers:
    - name: placeholder-container-name 
      image: innoagent/pod:1.1  # 변경된 이미지로 수정
```

### 4. 백엔드 서버 재시작
Pod 도커 이미지가 수정된다면 자율행동체 플랫폼 백엔드 서버 역시 재시작해야 합니다.
```bash
kubectl delete pod <backend-server-deploy-pod> -n <namespace>
```
