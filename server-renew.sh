#!/bin/bash

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 현재 프로젝트 루트 디렉토리 확인
PROJECT_ROOT=$(pwd)

echo -e "${BLUE}🚀 배포 스크립트 시작${NC}"

# 1. 버전 자동 증가
VERSION_FILE="$PROJECT_ROOT/.version"
if [ ! -f "$VERSION_FILE" ]; then
    echo "0.0.1" > "$VERSION_FILE"
fi

CURRENT_VERSION=$(cat "$VERSION_FILE")
echo -e "${YELLOW}현재 버전: $CURRENT_VERSION${NC}"

# 버전 증가 (패치 버전)
IFS='.' read -ra VERSION_PARTS <<< "$CURRENT_VERSION"
MAJOR=${VERSION_PARTS[0]}
MINOR=${VERSION_PARTS[1]}
PATCH=${VERSION_PARTS[2]}
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="$MAJOR.$MINOR.$NEW_PATCH"

echo "$NEW_VERSION" > "$VERSION_FILE"
echo -e "${GREEN}새 버전: $NEW_VERSION${NC}"

# 2. Docker 이미지 빌드
echo -e "${BLUE}📦 Docker 이미지 빌드 중...${NC}"
cd "$PROJECT_ROOT/backend_server"
docker build -t pbc9236/robot:v$NEW_VERSION . || {
    echo -e "${RED}❌ Docker 빌드 실패${NC}"
    exit 1
}

# 3. YAML 파일 업데이트
echo -e "${BLUE}📝 YAML 파일 업데이트 중...${NC}"
cd "$PROJECT_ROOT/k8s"

# sed를 사용해서 이미지 태그 자동 변경
sed -i.bak "s|image: pbc9236/robot:v.*|image: pbc9236/robot:v$NEW_VERSION|g" server-deploy.yaml

echo -e "${GREEN}✅ 이미지 태그 업데이트: pbc9236/robot:v$NEW_VERSION${NC}"

# 4. Kubernetes 배포
echo -e "${BLUE}🚁 Kubernetes 배포 중...${NC}"
kubectl apply -f server-deploy.yaml || {
    echo -e "${RED}❌ Kubernetes 배포 실패${NC}"
    exit 1
}

# 5. 배포 완료 대기
echo -e "${BLUE}⏳ 배포 완료 대기 중...${NC}"
kubectl rollout status deployment/robot-deploy -n robot --timeout=300s

# 6. 로그 확인 (옵션)
echo -e "${YELLOW}📋 로그를 확인하시겠습니까? (y/N)${NC}"
read -r SHOW_LOGS

if [[ $SHOW_LOGS =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}📊 로그 실시간 확인 중... (Ctrl+C로 종료)${NC}"
    kubectl logs -f deployment/robot-deploy -n robot
else
    echo -e "${GREEN}🎉 배포 완료! 수동으로 로그 확인: kubectl logs -f deployment/robot-deploy -n robot${NC}"
fi