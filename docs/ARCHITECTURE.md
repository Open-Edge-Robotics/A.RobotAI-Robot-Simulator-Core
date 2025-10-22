# Autonomous Agent Simulation Management System 설치 & 배포 가이드

**Version:** v1.0
**Generated:** 2025-10-10

---

## 1. 핵심 구성 요소

* **FastAPI 애플리케이션**

  * **위치:** `backend_server/src/main.py` (`app` 선언)
  * **용도:** REST API 제공 (템플릿, 시뮬레이션, 인스턴스, 인증 등)
  * **실행:** Kubernetes Pod 형태 (`kubectl apply -f k8s/server-deploy.yaml`)
  * **코드 변경 반영:** `server-renew.sh` 사용 → 이미지 빌드 → YAML 업데이트 → 재배포

* **데이터베이스 (PostgreSQL)**

  * **용도:** 영속적 데이터 저장 (시뮬레이션 메타데이터, 유저, 템플릿 등)
  * **접근:** SQLAlchemy / SQLModel ORM
  * **실행:** Kubernetes StatefulSet + PVC

* **캐시 (Redis)**

  * **용도:** 시뮬레이션 상태 관리, 리프레시 토큰 관리
  * **접근:** Redis 클라이언트 사용
  * **실행:** Kubernetes StatefulSet + PVC

* **오브젝트 스토리지 (MinIO, S3 호환)**

  * **용도:** 시뮬레이션 재생에 필요한 rosbag 파일 묶음(`metadata.yaml`, `.db3` 파일) 저장
  * **접근:** MinIO Python SDK (`minio` 의존성)
  * **실행:** Kubernetes StatefulSet + PVC

* **Metrics 서버**

  * **용도:** HPA 및 CPU/메모리 메트릭 수집
  * **실행:** Kubernetes Pod

---

## 2. 런타임 토폴로지

### 2.1 로컬 환경 (개발)

* **FastAPI 실행:** Kubernetes Pod (`kubectl apply -f k8s/server-deploy.yaml`)
* **DB / 캐시 / 스토리지 / Metrics 서버:** 모두 Kubernetes Pod 형태
* **코드 변경 후 반영:** `server-renew.sh`

  1. 버전 자동 증가 (`.version` 파일)
  2. Docker 이미지 빌드 (`pbc9236/robot:v<new_version>`)
  3. `server-deploy.yaml` 이미지 태그 업데이트
  4. Kubernetes 재배포 (`kubectl apply -f server-deploy.yaml`)
  5. 배포 완료 대기 (`kubectl rollout status`)
  6. 로그 확인 옵션 제공
* **목적:** 개발/테스트 환경에서 실제 클러스터 구조 그대로 FastAPI 포함 전체 스택 운영

### 2.2 클러스터 환경 (프로덕션 / 스테이징)

* **컴포넌트 배포:** Deployment / StatefulSet / Service 사용
* **PostgreSQL:** StatefulSet + PVC
* **Redis:** StatefulSet (또는 관리형 Redis) + PVC
* **MinIO:** StatefulSet (단일/분산 모드) + PVC
* **FastAPI:** Deployment + HPA
* **Metrics 서버:** 클러스터 설치, HPA 및 CPU/메모리 메트릭 제공

---

## 3. 설치 및 배포 방법

### 3.1 네임스페이스 생성 (필수)

```bash
kubectl create namespace robot
```

* 생성하지 않으면 FastAPI Pod 및 기타 리소스 생성 시 에러 발생

### 3.2 매니페스트 적용 순서 (중요)

1. **PostgreSQL**

   * PV 생성: `postgres-pv.yaml`
   * Deployment/Service 생성: `postgres-deploy.yaml`
2. **MinIO**

   * PV 생성: `minio-pv.yaml`
   * Deployment/Service 생성: `minio-deploy.yaml`
3. **Redis / Metrics 서버 / FastAPI**

   * Redis: StatefulSet 배포
   * Metrics 서버: Pod 배포
   * FastAPI: Deployment 배포 (`server-deploy.yaml`)

**적용 예시**

```bash
kubectl apply -f k8s/postgres-pv.yaml
kubectl apply -f k8s/postgres-deploy.yaml
kubectl apply -f k8s/minio-pv.yaml
kubectl apply -f k8s/minio-deploy.yaml
kubectl apply -f k8s/redis-deploy.yaml
kubectl apply -f k8s/metrics-server.yaml
kubectl apply -f k8s/server-deploy.yaml
```

### 3.3 자동 설치 스크립트

```bash
./install.sh
```

* PostgreSQL / MinIO / Redis / Backend Server / Metrics 서버 배포
* 서비스 상태 확인 및 연결 테스트 포함

### 3.4 FastAPI 코드 변경 후 재배포

```bash
./server-renew.sh
```

* 버전 자동 증가 → Docker 이미지 빌드 → YAML 업데이트 → Kubernetes 재배포
* 배포 완료 후 로그 확인 가능

---

## 4. 컴포넌트 간 상호작용 (데이터 흐름)

1. **클라이언트 → FastAPI**

   * HTTP(S) 요청 전송
   * 토큰 기반 인증 처리

2. **FastAPI → PostgreSQL**

   * 시뮬레이션 메타데이터, 템플릿, 유저 데이터 CRUD
   * 트랜잭션/세션 관리: SQLAlchemy / SQLModel

3. **FastAPI → Redis**

   * 실시간 상태 관리, 캐시 용도
   * 예: 시뮬레이션 실행 상태(대기/실행/완료) 저장, 리프레시 토큰 관리
   * Pub/Sub 이벤트 전파 가능

4. **FastAPI → MinIO**

   * 시뮬레이션 재생에 필요한 rosbag 파일 묶음(`metadata.yaml`, `.db3`) 업로드/조회

5. **Kubernetes (옵션)**

   * FastAPI Pod는 Service를 통해 내부/외부 노출
   * HPA는 metrics-server를 통해 Pod 메트릭 조회 후 자동 스케일링

---

## 5. Kubernetes 배포 세부사항

* **`k8s/` 폴더 구성 예시**

  * `postgres-deploy.yaml` — PostgreSQL Deployment/StatefulSet + Service
  * `redis-deploy.yaml` — Redis Deployment/StatefulSet + Service
  * `minio-deploy.yaml` — MinIO Deployment/StatefulSet + Service
  * `server-deploy.yaml` — Backend Deployment + Service + ConfigMap/Secrets
  * `metrics-server-dev.yaml` — 개발용 metrics-server 설치

* **metrics-server 개발용 플래그**

  * `--metric-resolution=15s` — 메트릭 수집 주기 15초
  * `--kubelet-insecure-tls` — kubelet 인증서 검증 비활성화 (개발/테스트 환경 전용)
  * `--kubelet-preferred-address-types=InternalIP,Hostname,InternalDNS,ExternalDNS,ExternalIP`
  * `--kubelet-use-node-status-port`
  * ⚠️ 프로덕션 환경에서는 `--kubelet-insecure-tls` 사용 금지

---

## 6. 설치/배포 핵심 주의사항

* **네임스페이스 먼저 생성**: `robot`
* **PV 생성 → Deployment 적용 순서 필수** (특히 PostgreSQL, MinIO)
* FastAPI, Redis, Metrics 서버는 순서 유연하지만 배포 후 상태 확인 권장
* 코드 변경 시 `server-renew.sh`로 재배포