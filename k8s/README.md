# Kubernetes 배포 스크립트
## 사용 가이드

**해당 스크립트는 기본적으로 Openstack 기반 Ubuntu 환경에서 실행할 수 있습니다.**

### 1. 스크립트 저장
각 파일을 Openstack 인스턴스에 저장합니다.

### 2. Kubernetes 배포
각 파일을 쿠버네티스 환경에 배포하여 실행할 수 있도록 합니다.
```bash
kubectl apply -f <file-name>.yaml
```

## PostgreSQL

| 파일명                    | 종류                                      |
|------------------------|-----------------------------------------|
| `postgrs-pv.yaml`      | PersistentVolume, PersistentVolumeClaim |
| `postgres-deploy.yaml` | Deployment, Service                     |
| `postgres-secret.yaml` | Secret (내부 공유)                          |


## MinIO

| 파일명                 | 종류                                      |
|---------------------|-----------------------------------------|
| `minio-pv.yaml`     | PersistentVolume, PersistentVolumeClaim |
| `minio-deploy.yaml` | Deployment, Service                     |
| `minio-secret.yaml` | Secret (내부 공유)                          |


## Backend Server

| 파일명                  | 종류                  |
|----------------------|---------------------|
| `server-deploy.yaml` | Deployment, Service |
| `server-secret.yaml` | Secret (내부 공유)      |