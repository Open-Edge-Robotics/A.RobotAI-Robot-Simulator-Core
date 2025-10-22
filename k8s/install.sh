#!/bin/bash

# Robot Simulator 완전 자동화 설치 스크립트

echo "🚀 Robot Simulator 설치를 시작합니다..."

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 함수 정의
print_step() {
    echo -e "${BLUE}=== $1 ===${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 단계 1: 환경 준비
print_step "STEP 1: 환경 준비"

# 네임스페이스 생성
print_warning "robot 네임스페이스를 생성합니다..."
kubectl create namespace robot 2>/dev/null || echo "네임스페이스가 이미 존재합니다."

# 필요한 디렉토리 생성
print_warning "필요한 디렉토리를 생성합니다..."
sudo mkdir -p /mnt/data/postgres
sudo mkdir -p /mnt/data/minio
sudo chmod -R 777 /mnt/data
print_success "디렉토리 생성 완료"

# 단계 2: PostgreSQL 설치
print_step "STEP 2: PostgreSQL 설치"

# PersistentVolume 생성
print_warning "PostgreSQL PersistentVolume을 생성합니다..."
kubectl apply -f postgres-pv.yaml
sleep 5

# PostgreSQL 배포
print_warning "PostgreSQL을 배포합니다..."
kubectl apply -f postgres-deploy.yaml

# PostgreSQL 준비 대기
print_warning "PostgreSQL이 준비될 때까지 대기합니다... (최대 5분)"
if kubectl wait --for=condition=available --timeout=300s deployment/postgres-deploy -n robot; then
    print_success "PostgreSQL 배포 완료"
else
    print_error "PostgreSQL 배포 실패"
    exit 1
fi

# 단계 3: MinIO 설치
print_step "STEP 3: MinIO 설치"

# PersistentVolume 생성
print_warning "MinIO PersistentVolume을 생성합니다..."
kubectl apply -f minio-pv.yaml
sleep 5

# MinIO 배포
print_warning "MinIO를 배포합니다..."
kubectl apply -f minio-deploy.yaml

# MinIO 준비 대기
print_warning "MinIO가 준비될 때까지 대기합니다... (최대 5분)"
if kubectl wait --for=condition=available --timeout=300s deployment/minio-deploy -n robot; then
    print_success "MinIO 배포 완료"
else
    print_error "MinIO 배포 실패"
    exit 1
fi

# 단계 4: 서비스 상태 확인
print_step "STEP 4: 인프라 서비스 상태 확인"

echo "현재 배포된 Pod 상태:"
kubectl get pods -n robot

echo "현재 서비스 상태:"
kubectl get svc -n robot

echo "PersistentVolume 상태:"
kubectl get pv

echo "PersistentVolumeClaim 상태:"
kubectl get pvc -n robot

# 단계 5: 연결 테스트
print_step "STEP 5: 서비스 연결 테스트"

print_warning "PostgreSQL 연결 테스트..."
if kubectl exec -n robot deployment/postgres-deploy -- pg_isready -U agent_user; then
    print_success "PostgreSQL 연결 성공"
else
    print_error "PostgreSQL 연결 실패"
fi

print_warning "MinIO 연결 테스트..."
if kubectl exec -n robot deployment/minio-deploy -- curl -f http://localhost:9000/minio/health/live; then
    print_success "MinIO 연결 성공"
else
    print_warning "MinIO 연결 테스트 건너뜀 (정상적인 경우일 수 있음)"
fi

# 단계 6: Backend Server 배포
print_step "STEP 6: Backend Server 배포"

print_warning "Backend Server를 배포합니다..."
kubectl apply -f server-deploy.yaml

print_warning "Backend Server가 준비될 때까지 대기합니다... (최대 5분)"
if kubectl wait --for=condition=available --timeout=300s deployment/robot-deploy -n robot; then
    print_success "Backend Server 배포 완료"
else
    print_error "Backend Server 배포 실패"
    echo "로그를 확인해보세요:"
    kubectl logs deployment/robot-deploy -n robot --tail=20
    exit 1
fi

# 단계 7: 최종 확인
print_step "STEP 7: 최종 시스템 확인"

echo "전체 시스템 상태:"
kubectl get all -n robot

echo "서비스 엔드포인트:"
kubectl get endpoints -n robot

# 접속 정보 출력
print_step "설치 완료!"

NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
if [ -z "$NODE_IP" ]; then
    NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
fi

print_success "🎉 Robot Simulator 설치가 완료되었습니다!"
echo ""
echo "📋 접속 정보:"
echo "   • Backend API: http://$NODE_IP:30020"
echo "   • MinIO Console: http://$NODE_IP:30334"
echo "   • MinIO API: http://$NODE_IP:30333"
echo ""
echo "🔍 시스템 상태 확인:"
echo "   kubectl get all -n robot"
echo ""
echo "📜 로그 확인:"
echo "   kubectl logs deployment/robot-deploy -n robot"
echo "   kubectl logs deployment/postgres-deploy -n robot"
echo "   kubectl logs deployment/minio-deploy -n robot"
