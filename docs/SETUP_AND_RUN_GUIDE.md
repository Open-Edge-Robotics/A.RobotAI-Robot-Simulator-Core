# Autonomous Agent Simulation Management System 설치 & 배포 가이드

**Version:** v1.0
**Generated:** 2025-10-14

---

이 문서는 `robot-simulator-back` 프로젝트를 로컬 개발 환경부터 Kubernetes 프로덕션 배포까지 전체 과정을 단계별로 안내합니다.

## 목차

1. [개요](#1-개요)
2. [아키텍처 및 데이터 흐름](#2-아키텍처-및-데이터-흐름)
3. [환경 준비](#3-환경-준비)
4. [로컬 개발 환경 설정](#4-로컬-개발-환경-설정)
5. [Docker를 이용한 로컬 실행](#5-docker를-이용한-로컬-실행)
6. [Kubernetes 배포](#6-kubernetes-배포)
7. [검증 및 헬스체크](#7-검증-및-헬스체크)
8. [문제 해결](#8-문제-해결)
9. [빠른 시작 가이드](#9-빠른-시작-가이드)

---

## 1. 개요

### 1.1 주요 구성요소

- **FastAPI**: 웹 프레임워크 (진입점: `backend_server/src/main.py`)
- **SQLAlchemy / SQLModel**: ORM 및 모델
- **PostgreSQL**: 시뮬레이션 메타데이터, 템플릿, 유저 데이터 저장
- **Redis**: 실시간 상태 관리 및 캐시
- **MinIO**: S3 호환 오브젝트 스토리지 (rosbag 파일 저장)
- **Kubernetes**: 컨테이너 오케스트레이션 및 배포
- **Alembic**: 데이터베이스 마이그레이션 (선택)

### 1.2 시스템 목적

FastAPI 애플리케이션이 로봇 시뮬레이션 관련 API를 제공하며:
- **PostgreSQL**은 시뮬레이션 메타데이터 영속화
- **Redis**는 실시간 상태 및 캐시 관리
- **MinIO**는 시뮬레이션 데이터(rosbag) 저장

로컬 개발은 가상환경 또는 Docker Compose로 수행하고, 프로덕션은 컨테이너화된 이미지를 Kubernetes에 배포합니다.

---

## 2. 아키텍처 및 데이터 흐름

### 2.1 컴포넌트 간 상호작용

```
┌─────────────┐
│   Client    │
│ (HTTP/HTTPS)│
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│            FastAPI Server               │
│  - 인증/인가 (토큰 기반)                │
│  - API 엔드포인트 제공                  │
│  - 비즈니스 로직 처리                   │
└────┬────────┬────────┬──────────────────┘
     │        │        │
     ▼        ▼        ▼
┌──────┐  ┌──────┐  ┌──────┐
│ PG   │  │Redis │  │MinIO │
│ SQL  │  │      │  │      │
└──────┘  └──────┘  └──────┘
메타데이터   상태/캐시   파일저장
```

### 2.2 데이터 흐름 상세

1. **클라이언트 → FastAPI**
   - HTTP(S) 요청 전송
   - JWT 토큰 기반 인증 처리

2. **FastAPI → PostgreSQL**
   - 시뮬레이션 메타데이터, 템플릿, 유저 데이터 CRUD
   - SQLAlchemy/SQLModel을 통한 트랜잭션 관리

3. **FastAPI → Redis**
   - 실시간 시뮬레이션 상태 관리 (대기/실행/완료)
   - 리프레시 토큰 저장
   - Pub/Sub를 통한 이벤트 전파 (옵션)

4. **FastAPI → MinIO**
   - rosbag 파일 묶음 업로드/다운로드
   - `metadata.yaml`, `.db3` 파일 관리

5. **Kubernetes (프로덕션)**
   - Service를 통한 내부/외부 노출
   - HPA(Horizontal Pod Autoscaler)를 통한 자동 스케일링
   - metrics-server 기반 리소스 모니터링

---

## 3. 환경 준비

### 3.1 권장 사양

| 구성요소 | 버전 |
|---------|------|
| Python | 3.11+ |
| PostgreSQL | 14+ |
| Redis | 6+ |
| MinIO | 최신 권장 |
| Docker | 20.10+ |
| kubectl | 1.25+ |
| helm | v3+ |
| minikube/kind | 최신 |

### 3.2 필수 도구 설치

```bash
# kubectl 설치 (Linux)
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# helm 설치
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# minikube 설치 (로컬 k8s 클러스터)
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
```

---

## 4. 로컬 개발 환경 설정

> 💡 **권장 개발 방식**: 이 프로젝트는 로컬 개발 환경에서도 Kubernetes를 사용합니다. 프로덕션과 동일한 환경에서 개발하므로 환경 차이로 인한 문제를 예방할 수 있습니다.

### 4.1 Kubernetes 기반 로컬 개발 (권장)

로컬에서도 Kubernetes를 사용하여 개발합니다. [6. Kubernetes 배포](#6-kubernetes-배포) 섹션을 참고하세요.

### 4.2 가상환경 기반 개발 (선택사항)

FastAPI만 로컬에서 실행하고 싶은 경우:

**Linux/Mac (Bash)**

```bash
cd /path/to/robot-simulator-back
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
cd C:\path\to\robot-simulator-back
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4.3 환경 변수 설정 (가상환경 사용 시)

FastAPI를 로컬에서 직접 실행하는 경우, `.env` 파일을 프로젝트 루트에 생성:

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/robotdb

# Redis
REDIS_URL=redis://localhost:6379/0

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=robot-sim
MINIO_USE_SSL=false

# API
API_STR=/api/v1

# JWT (옵션)
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

> **참고**: Kubernetes 기반 개발 시에는 ConfigMap/Secret에서 환경 변수를 관리합니다.

### 4.4 데이터베이스 마이그레이션 (Alembic)

프로젝트에 Alembic 설정이 있는 경우:

```bash
# 마이그레이션 적용
alembic upgrade head
```

Alembic 설정이 없는 경우 초기화:

```bash
# Alembic 초기화
alembic init alembic

# alembic.ini에서 DATABASE_URL 설정
# alembic/env.py에서 Base 모델 import

# 초기 마이그레이션 생성
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

> **참고**: 이 프로젝트는 `backend_server/src/main.py`의 `init_db()` 함수에서 DB 초기화를 수행할 수 있습니다.

### 4.4 애플리케이션 실행

**방법 1: 직접 실행**

```bash
cd backend_server/src
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**방법 2: PYTHONPATH 지정**

```bash
# 프로젝트 루트에서
PYTHONPATH=backend_server/src uvicorn backend_server.src.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4.5 로컬 확인

```bash
# 기본 엔드포인트
curl http://127.0.0.1:8000/

# Redis 헬스체크
curl http://127.0.0.1:8000/health/redis

# API 문서 (Swagger UI)
# 브라우저: http://127.0.0.1:8000/docs
```

---

## 5. Docker Compose 사용 (선택사항)

> ⚠️ **참고**: 이 프로젝트는 로컬 개발에서도 Kubernetes를 사용합니다. Docker Compose는 선택사항입니다.

Docker Compose를 사용하고 싶은 경우에만 참고하세요.

### 5.1 Docker 이미지 빌드

```bash
docker build -t robot-sim-backend:latest -f backend_server/Dockerfile .
```

### 5.2 Docker 네트워크 및 서비스 구성

```bash
# 네트워크 생성
docker network create robot-net

# PostgreSQL 실행
docker run -d --name robot-postgres \
  --network robot-net \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=robotdb \
  -p 5432:5432 \
  postgres:15

# Redis 실행
docker run -d --name robot-redis \
  --network robot-net \
  -p 6379:6379 \
  redis:7

# MinIO 실행
docker run -d --name robot-minio \
  --network robot-net \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# Backend 실행
docker run -d --name robot-backend \
  --network robot-net \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://postgres:password@robot-postgres:5432/robotdb \
  -e REDIS_URL=redis://robot-redis:6379/0 \
  -e MINIO_ENDPOINT=robot-minio:9000 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -e MINIO_BUCKET=robot-sim \
  robot-sim-backend:latest
```

### 5.3 Docker Compose (권장)

`docker-compose.yml` 파일 생성:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    container_name: robot-postgres
    environment:
      POSTGRES_DB: robotdb
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    container_name: robot-redis
    ports:
      - "6379:6379"

  minio:
    image: minio/minio
    container_name: robot-minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data

  backend:
    build:
      context: .
      dockerfile: backend_server/Dockerfile
    container_name: robot-backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:password@postgres:5432/robotdb
      REDIS_URL: redis://redis:6379/0
      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
      MINIO_BUCKET: robot-sim
    depends_on:
      - postgres
      - redis
      - minio

volumes:
  postgres_data:
  minio_data:
```

실행:

```bash
docker-compose up -d
```

---

## 6. Kubernetes 배포 (로컬 개발 & 프로덕션)

> 💡 **이 프로젝트의 표준 개발 방식**: 로컬 개발부터 프로덕션까지 모두 Kubernetes를 사용합니다.

### 6.1 사전 준비

#### 6.1.1 로컬 Kubernetes 클러스터 시작

**Minikube 사용 (권장)**

```bash
minikube start --driver=docker --memory=4096 --cpus=2
```

**Kind 사용 (대안)**

```bash
kind create cluster --name robot-dev
```

#### 6.1.2 네임스페이스 생성 (필수)

```bash
kubectl create namespace robot
```

> ⚠️ **중요**: 네임스페이스를 먼저 생성하지 않으면 모든 리소스 생성이 실패합니다.

### 6.2 Kubernetes 매니페스트 구조

프로젝트의 `k8s/` 디렉토리 구성:

```
k8s/
├── postgres-pv.yaml          # PostgreSQL PersistentVolume
├── postgres-deploy.yaml      # PostgreSQL Deployment/Service
├── minio-pv.yaml             # MinIO PersistentVolume
├── minio-deploy.yaml         # MinIO Deployment/Service
├── redis-deploy.yaml         # Redis StatefulSet/Service
├── server-deploy.yaml        # FastAPI Deployment/Service/ConfigMap
└── metrics-server-dev.yaml   # Metrics Server (개발용)
```

### 6.3 배포 순서 (중요)

**순서를 반드시 지켜야 합니다**:

#### Step 1: PostgreSQL 배포

```bash
# PersistentVolume 생성
kubectl apply -f k8s/postgres-pv.yaml

# Deployment 및 Service 생성
kubectl apply -f k8s/postgres-deploy.yaml

# 상태 확인
kubectl get pods -n robot | grep postgres
kubectl get svc -n robot | grep postgres
```

#### Step 2: MinIO 배포

```bash
# PersistentVolume 생성
kubectl apply -f k8s/minio-pv.yaml

# Deployment 및 Service 생성
kubectl apply -f k8s/minio-deploy.yaml

# 상태 확인
kubectl get pods -n robot | grep minio
kubectl get svc -n robot | grep minio
```

#### Step 3: Redis 배포

```bash
# StatefulSet 및 Service 생성
kubectl apply -f k8s/redis-deploy.yaml

# 상태 확인
kubectl get pods -n robot | grep redis
kubectl get svc -n robot | grep redis
```

#### Step 4: Metrics Server 배포 (HPA 사용 시 필수)

```bash
kubectl apply -f k8s/metrics-server-dev.yaml

# 상태 확인
kubectl get pods -n kube-system | grep metrics-server
```

> ⚠️ **개발환경 전용 설정**:
> - `--kubelet-insecure-tls`: TLS 검증 비활성화
> - `--metric-resolution=15s`: 15초 주기로 메트릭 수집
> - **프로덕션에서는 절대 사용 금지**

#### Step 5: FastAPI Backend 배포

```bash
# Deployment 및 Service 생성
kubectl apply -f k8s/server-deploy.yaml

# 상태 확인
kubectl get pods -n robot | grep backend
kubectl get svc -n robot | grep backend
```

### 6.4 자동 배포 스크립트

프로젝트에 `install.sh` 스크립트가 있는 경우:

```bash
chmod +x install.sh
./install.sh
```

스크립트는 다음을 수행합니다:
- 네임스페이스 생성
- 모든 리소스 순차 배포
- Pod 상태 확인
- 서비스 연결 테스트

### 6.5 FastAPI 코드 변경 후 재배포

`server-renew.sh` 스크립트 사용:

```bash
chmod +x server-renew.sh
./server-renew.sh
```

스크립트 동작:
1. 이미지 버전 자동 증가
2. Docker 이미지 빌드
3. YAML 파일 업데이트
4. Kubernetes 재배포
5. 배포 완료 후 로그 확인

### 6.6 서비스 노출

#### 포트 포워딩 (개발용)

```bash
# FastAPI 서비스
kubectl port-forward svc/robot-backend-service 8000:8000 -n robot

# MinIO UI
kubectl port-forward svc/minio-service 9000:9000 -n robot
kubectl port-forward svc/minio-service 9001:9001 -n robot
```

#### NodePort 사용 (Minikube)

```bash
minikube service robot-backend-service -n robot
```

#### LoadBalancer (클라우드 환경)

`server-deploy.yaml`에서 Service 타입을 `LoadBalancer`로 변경

---

## 7. 검증 및 헬스체크

### 7.1 기본 엔드포인트 테스트

```bash
# 루트 엔드포인트
curl http://127.0.0.1:8000/

# 예상 응답: {"message": "Robot Simulator Backend API"}
```

### 7.2 헬스체크 엔드포인트

```bash
# Redis 연결 확인
curl http://127.0.0.1:8000/health/redis
# 예상 응답: {"status": "healthy", "redis": "connected"}

# PostgreSQL 연결 확인
curl http://127.0.0.1:8000/health/db
# 예상 응답: {"status": "healthy", "database": "connected"}
```

### 7.3 MinIO 연결 확인

```bash
# MinIO UI 접속
# 브라우저: http://localhost:9000
# 로그인: minioadmin / minioadmin

# 또는 curl로 확인
curl http://localhost:9000/minio/health/live
```

### 7.4 Kubernetes Pod 로그 확인

```bash
# FastAPI Pod 로그
kubectl logs -f deployment/robot-backend -n robot

# PostgreSQL Pod 로그
kubectl logs -f deployment/postgres -n robot

# 전체 Pod 상태
kubectl get pods -n robot -o wide
```

### 7.5 메트릭 확인 (HPA 설정 시)

```bash
# Pod 메트릭
kubectl top pods -n robot

# Node 메트릭
kubectl top nodes
```

---

## 8. 문제 해결

### 8.1 일반적인 문제

#### 패키지 설치 실패

```bash
# 캐시 없이 재설치
pip install --no-cache-dir -r requirements.txt

# pip 업그레이드
python -m pip install --upgrade pip setuptools wheel
```

#### PYTHONPATH 문제

```bash
# 환경변수 설정
export PYTHONPATH="${PYTHONPATH}:$(pwd)/backend_server/src"

# 또는 .bashrc/.zshrc에 추가
echo 'export PYTHONPATH="${PYTHONPATH}:${HOME}/robot-simulator-back/backend_server/src"' >> ~/.bashrc
```

#### Alembic 설정 없음

```bash
# 초기화
alembic init alembic

# env.py 수정 필요:
# - config.get_main_option("sqlalchemy.url") 대신 환경변수 사용
# - target_metadata에 Base.metadata 설정
```

### 8.2 Docker 관련 문제

#### 포트 충돌

```bash
# 사용 중인 포트 확인
lsof -i :8000
lsof -i :5432

# 프로세스 종료
kill -9 <PID>
```

#### 컨테이너 재시작

```bash
# 전체 재시작
docker-compose down
docker-compose up -d

# 특정 서비스만
docker-compose restart backend
```

### 8.3 Kubernetes 관련 문제

#### Pod가 Pending 상태

```bash
# 상세 정보 확인
kubectl describe pod <pod-name> -n robot

# 일반적인 원인:
# - PersistentVolume 바인딩 실패
# - 리소스 부족
# - 노드 스케줄링 실패
```

#### PersistentVolume 문제

```bash
# PV/PVC 상태 확인
kubectl get pv
kubectl get pvc -n robot

# StorageClass 확인
kubectl get storageclass

# Minikube의 경우 hostPath 사용
# 디렉토리 권한 확인 필요
```

#### ImagePullBackOff 에러

```bash
# 이미지 확인
kubectl describe pod <pod-name> -n robot

# Minikube에서 로컬 이미지 사용
eval $(minikube docker-env)
docker build -t robot-sim-backend:latest .

# YAML에서 imagePullPolicy: Never 설정
```

#### 서비스 연결 불가

```bash
# 서비스 엔드포인트 확인
kubectl get endpoints -n robot

# DNS 테스트
kubectl run -it --rm debug --image=busybox --restart=Never -n robot -- nslookup postgres-service

# 포트 확인
kubectl get svc -n robot
```

#### Metrics Server 에러

```bash
# 로그 확인
kubectl logs -n kube-system deployment/metrics-server

# 재시작
kubectl rollout restart deployment/metrics-server -n kube-system

# 메트릭 API 확인
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml
```

### 8.4 데이터베이스 문제

#### 연결 실패

```bash
# 연결 문자열 확인
echo $DATABASE_URL

# PostgreSQL 직접 연결 테스트
psql "postgresql://postgres:password@localhost:5432/robotdb"

# Docker 내부에서 테스트
docker exec -it robot-postgres psql -U postgres -d robotdb
```

#### 마이그레이션 실패

```bash
# 현재 버전 확인
alembic current

# 마이그레이션 히스토리
alembic history

# 특정 버전으로 다운그레이드
alembic downgrade <revision>

# 강제 업그레이드
alembic upgrade head --sql  # SQL만 출력
alembic upgrade head
```

---

## 9. 빠른 시작 가이드

### 9.1 Kubernetes 기반 개발 환경 (권장 - 10분)

> 💡 **표준 개발 방식**: 로컬에서도 Kubernetes를 사용합니다.

```bash
# 1. 저장소 클론
git clone <repository-url>
cd robot-simulator-back

# 2. Minikube 시작
minikube start --driver=docker --memory=4096 --cpus=2

# 3. 네임스페이스 생성
kubectl create namespace robot

# 4. 자동 설치 스크립트 실행
chmod +x install.sh
./install.sh

# 5. 포트 포워딩 (개발 시 편의를 위해)
kubectl port-forward svc/robot-backend-service 8000:8000 -n robot &

# 6. 확인
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/health/redis

# 7. 코드 수정 후 재배포
# 코드 변경 시:
./server-renew.sh
```

### 9.2 가상환경 기반 개발 (선택사항 - 5분)

FastAPI만 로컬에서 실행하고 싶은 경우:

```bash
# 1. 저장소 클론
git clone <repository-url>
cd robot-simulator-back

# 2. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 설정
cat > .env << EOF
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/robotdb
REDIS_URL=redis://localhost:6379/0
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=robot-sim
API_STR=/api/v1
EOF

# 5. 의존 서비스 시작 (Kubernetes 또는 Docker 필요)
# Kubernetes 사용:
minikube start
kubectl apply -f k8s/postgres-deploy.yaml
kubectl apply -f k8s/redis-deploy.yaml
kubectl apply -f k8s/minio-deploy.yaml
kubectl port-forward svc/postgres-service 5432:5432 -n robot &
kubectl port-forward svc/redis-service 6379:6379 -n robot &
kubectl port-forward svc/minio-service 9000:9000 -n robot &

# 또는 Docker 사용:
# docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=password postgres:15
# docker run -d -p 6379:6379 redis:7
# docker run -d -p 9000:9000 minio/minio server /data

# 6. DB 마이그레이션 (필요시)
alembic upgrade head

# 7. 서버 실행
cd backend_server/src
uvicorn main:app --reload

# 8. 확인
curl http://127.0.0.1:8000/
```

### 9.3 Docker Compose (선택사항 - 3분)

```bash
# 1. 저장소 클론
git clone <repository-url>
cd robot-simulator-back

# 2. 전체 스택 실행
docker-compose up -d

# 3. 확인
curl http://127.0.0.1:8000/health/redis
```

### 9.3 Kubernetes (10분 설정)

```bash
# 1. Minikube 시작
minikube start --driver=docker

# 2. 네임스페이스 생성
kubectl create namespace robot

# 3. 자동 설치 스크립트 실행
chmod +x install.sh
./install.sh

# 4. 포트 포워딩
kubectl port-forward svc/robot-backend-service 8000:8000 -n robot

# 5. 확인
curl http://127.0.0.1:8000/
```

### 9.4 코드 변경 후 재배포

```bash
# 코드 수정 후
./server-renew.sh

# 또는 수동으로
docker build -t robot-sim-backend:v2 .
kubectl set image deployment/robot-backend robot-backend=robot-sim-backend:v2 -n robot
kubectl rollout status deployment/robot-backend -n robot
```

---

## 10. 추가 권장 작업

### 10.1 프로덕션 배포 체크리스트

- [ ] Secrets 관리 (Kubernetes Secrets 또는 Vault)
- [ ] Liveness/Readiness Probe 설정
- [ ] Resource Limits/Requests 설정
- [ ] Ingress 설정 (HTTPS)
- [ ] 로깅 및 모니터링 (Prometheus, Grafana)
- [ ] 백업 전략 (PostgreSQL, MinIO)
- [ ] TLS 인증서 설정 (cert-manager)
- [ ] Network Policy 적용
- [ ] RBAC 설정

### 10.2 CI/CD 파이프라인

```yaml
# .github/workflows/deploy.yml 예시
name: Deploy to Kubernetes

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Build Docker Image
        run: docker build -t robot-sim-backend:${{ github.sha }} .
      
      - name: Push to Registry
        run: |
          docker tag robot-sim-backend:${{ github.sha }} registry.example.com/robot-sim-backend:${{ github.sha }}
          docker push registry.example.com/robot-sim-backend:${{ github.sha }}
      
      - name: Deploy to Kubernetes
        run: |
          kubectl set image deployment/robot-backend robot-backend=registry.example.com/robot-sim-backend:${{ github.sha }} -n robot
```

### 10.3 모니터링 설정

```bash
# Prometheus & Grafana 설치 (Helm)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring --create-namespace

# FastAPI에 Prometheus 메트릭 추가
# pip install prometheus-fastapi-instrumentator
```

---

## 11. 참고 자료

- [FastAPI 공식 문서](https://fastapi.tiangolo.com/)
- [Kubernetes 공식 문서](https://kubernetes.io/docs/)
- [SQLAlchemy 문서](https://docs.sqlalchemy.org/)
- [Alembic 문서](https://alembic.sqlalchemy.org/)
- [MinIO 문서](https://min.io/docs/minio/linux/index.html)

---

**마지막 업데이트**: 2025-10-14  
**문서 버전**: 1.0