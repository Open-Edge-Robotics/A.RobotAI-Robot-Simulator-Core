import asyncio
import logging
from kubernetes import client, config
from typing import Dict, Any, List
from crud.rosbag import RosService
from models.enums import SimulationStatus
from schemas.dashboard import ResourceUsage, ResourceUsageData, PodStatusData, StatusBreakdown

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Kubernetes 메트릭 수집 서비스(싱글톤)"""
    
    _instance = None  # 클래스 변수로 인스턴스 저장
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            # 최초 생성 시에만 실제 객체 생성
            cls._instance = super(MetricsCollector, cls).__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        """초기화 작업 (클라이언트 설정 등)"""
        try:
            # 클러스터 내부에서 실행 시
            config.load_incluster_config()
        except Exception:
            # 로컬 개발 환경
            config.load_kube_config()
        
        self.v1 = client.CoreV1Api()
        self.custom_api = client.CustomObjectsApi()
        logger.info("Kubernetes 클라이언트 초기화 완료")
    
    async def collect_dashboard_metrics(self, simulation) -> Dict[str, Any]:
        """대시보드용 메트릭 전체 수집"""
        try:
            logger.info(f"메트릭 수집 시작: namespace={simulation.namespace}")
            print(f"[DEBUG] ▶ Collecting dashboard metrics | namespace={simulation.namespace}")

            # 병렬로 메트릭 수집 (성능 최적화)
            resource_task = self._collect_resource_usage(simulation.namespace)
            pod_task = self._collect_pod_status(simulation.namespace)
            print(f"[DEBUG] 🧩 Resource & Pod tasks created")

            resource_usage, pod_status = await asyncio.gather(
                resource_task,
                pod_task,
                return_exceptions=True
            )
            print(f"[DEBUG] ✅ asyncio.gather completed")

            # 예외 처리
            if isinstance(resource_usage, Exception):
                logger.error(f"리소스 메트릭 수집 실패: {resource_usage}")
                print(f"[ERROR] ❌ Resource usage collection failed: {resource_usage}")
                resource_usage = self._get_default_resource_usage()

            if isinstance(pod_status, Exception):
                logger.error(f"Pod 메트릭 수집 실패: {pod_status}")
                print(f"[ERROR] ❌ Pod status collection failed: {pod_status}")
                pod_status = self._get_default_pod_status()

            logger.info(f"메트릭 수집 완료: namespace={simulation.namespace}")
            print(f"[DEBUG] ✅ Metrics collection finished | resource_usage={resource_usage} | pod_status={pod_status}")

            return {
                "resource_usage": resource_usage,
                "pod_status": pod_status
            }

        except Exception as e:
            logger.error(f"전체 메트릭 수집 실패: {e}")
            print(f"[ERROR] ❌ Total metrics collection failed: {type(e).__name__} | {e}")
            return {
                "resource_usage": self._get_default_resource_usage(),
                "pod_status": self._get_default_pod_status()
            }

    
    async def _collect_resource_usage(self, namespace: str) -> ResourceUsageData:
        """Kubernetes 메트릭 API에서 리소스 사용률 수집"""
        try:
            # 1️⃣ Pod 상태 먼저 확인
            pods = self.v1.list_namespaced_pod(namespace)
            
            running_pods = [p for p in pods.items if p.status.phase == "Running"]
            total_pods = len(pods.items)
            
            if total_pods == 0:
                logger.warning(f"네임스페이스 {namespace}에 Pod가 없음")
                return self._get_preparing_resource_usage("no_pods")
            
            if len(running_pods) == 0:
                logger.info(f"모든 Pod가 아직 Running 상태가 아님 (총 {total_pods}개)")
                return self._get_preparing_resource_usage("pods_starting")
            
            # 2️⃣ 메트릭 조회
            try:
                pod_metrics = self.custom_api.list_namespaced_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    namespace=namespace,
                    plural="pods"
                )
            except client.ApiException as e:
                if e.status == 404:
                    logger.error("Metrics Server를 찾을 수 없음")
                    return self._get_default_resource_usage("no_metrics_server")
                elif e.status == 403:
                    logger.error("메트릭 조회 권한 없음")
                    return self._get_default_resource_usage("permission_denied")
                else:
                    logger.error(f"Kubernetes API 오류: {e}")
                    return self._get_default_resource_usage("api_error")
            
            # 3️⃣ 메트릭이 아직 수집되지 않은 경우
            if not pod_metrics.get('items'):
                logger.warning(f"Running Pod는 있지만 메트릭이 아직 수집되지 않음")
                return self._get_preparing_resource_usage("metrics_collecting")
            
            logger.debug(f"Pod 메트릭 수집 완료: {len(pod_metrics.get('items', []))}개 Pod")
            
            # 4️⃣ CPU, 메모리 사용률 계산
            cpu_usage = self._calculate_cpu_usage(pod_metrics)
            memory_usage = self._calculate_memory_usage(pod_metrics)
            disk_usage = await self._calculate_disk_usage(namespace)
            
            # 5️⃣ 상태 및 메시지 생성 (모두 normal)
            cpu_status, cpu_message = self._get_resource_status_with_message(cpu_usage)
            memory_status, memory_message = self._get_resource_status_with_message(memory_usage)
            disk_status, disk_message = self._get_resource_status_with_message(disk_usage)
            
            logger.debug(f"리소스 사용률 - CPU: {cpu_usage:.1f}%, Memory: {memory_usage:.1f}%, Disk: {disk_usage:.1f}%")
            
            return ResourceUsageData(
                cpu=ResourceUsage(
                    usage_percent=round(cpu_usage, 1),
                    status=cpu_status,
                    message=cpu_message
                ),
                memory=ResourceUsage(
                    usage_percent=round(memory_usage, 1),
                    status=memory_status,
                    message=memory_message
                ),
                disk=ResourceUsage(
                    usage_percent=round(disk_usage, 1),
                    status=disk_status,
                    message=disk_message
                )
            )
            
        except Exception as e:
            logger.error(f"리소스 메트릭 수집 오류: {e}", exc_info=True)
            return self._get_default_resource_usage("error")
    
    async def _collect_pod_status(self, namespace: str) -> PodStatusData:
        """Pod 상태 정보 수집 (rosbag 상태 및 시뮬레이션 상태 반영)"""
        try:
            # Pod 목록 조회
            pods = self.v1.list_namespaced_pod(namespace)
            print(f"Pod 목록 조회 완료: {len(pods.items)}개 Pod")
            
            status_counts = {"pending": 0, "running": 0, "stopped": 0, "completed": 0, "failed": 0}
            total_count = len(pods.items)
            
            # 각 Pod 상태 분류
            for pod in pods.items:
                # Pod IP 추출
                pod_ip = pod.status.pod_ip if pod.status and pod.status.pod_ip else None
                
                status = await RosService.get_pod_rosbag_playing_status(pod_ip)
                status_counts[status] += 1
                print(f"Pod {pod.metadata.name}: {status}")
            
            # overall_health 계산
            running_related = status_counts["running"] + status_counts["completed"] + status_counts["failed"]
            overall_health = (status_counts["completed"] / running_related * 100) if running_related > 0 else 0.0
            
            print(f"Pod 상태 집계 - Completed: {status_counts['completed']}, Pending: {status_counts['pending']}, Running: {status_counts['running']}, Stopped: {status_counts['stopped']}, Failed: {status_counts['failed']}")
            
            return PodStatusData(
                total_count=total_count,
                overall_health_percent=round(overall_health, 2),
                status_breakdown={
                    status: StatusBreakdown(
                        count=count,
                        percentage=round(
                            (count / total_count * 100) if total_count > 0 else 0.0, 
                            2
                        )
                    )
                    for status, count in status_counts.items()
                }
            )
            
        except Exception as e:
            logger.error(f"Pod 상태 수집 오류: {e}")
            raise
    
    def _calculate_cpu_usage(self, pod_metrics: Dict) -> float:
        """CPU 사용률 계산"""
        try:
            total_cpu_millicores = 0
            container_count = 0
            
            for pod in pod_metrics.get("items", []):
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                
                for container in pod.get("containers", []):
                    cpu_str = container["usage"]["cpu"]  # 예: "250m" 또는 "0.25"
                    
                    # CPU 값을 millicores로 변환
                    if cpu_str.endswith('m'):
                        cpu_millicores = float(cpu_str[:-1])
                    elif cpu_str.endswith('n'):  # nanocores
                        cpu_millicores = float(cpu_str[:-1]) / 1_000_000
                    else:
                        cpu_millicores = float(cpu_str) * 1000  # cores to millicores
                    
                    total_cpu_millicores += cpu_millicores
                    container_count += 1
                    
                    logger.debug(f"Pod {pod_name} CPU: {cpu_str} -> {cpu_millicores}m")
            
            if container_count == 0:
                return 0.0
            
            # 평균 CPU 사용률을 백분율로 계산
            # 가정: 컨테이너당 1000m(1 CPU) 할당
            avg_cpu_millicores = total_cpu_millicores / container_count
            usage_percent = (avg_cpu_millicores / 1000) * 100
            
            return min(max(usage_percent, 0.0), 100.0)  # 0-100% 범위 제한
            
        except Exception as e:
            logger.error(f"CPU 사용률 계산 오류: {e}")
            return 0.0
    
    def _calculate_memory_usage(self, pod_metrics: Dict) -> float:
        """메모리 사용률 계산"""
        try:
            total_memory_bytes = 0
            container_count = 0
            
            for pod in pod_metrics.get("items", []):
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                
                for container in pod.get("containers", []):
                    memory_str = container["usage"]["memory"]  # 예: "256Mi", "268435456"
                    
                    # 메모리 값을 바이트로 변환
                    memory_bytes = self._parse_memory_string(memory_str)
                    total_memory_bytes += memory_bytes
                    container_count += 1
                    
                    logger.debug(f"Pod {pod_name} Memory: {memory_str} -> {memory_bytes} bytes")
            
            if container_count == 0:
                return 0.0
            
            # 평균 메모리 사용량 계산
            # 가정: 컨테이너당 1GB 할당
            avg_memory_bytes = total_memory_bytes / container_count
            allocated_memory_bytes = 1024 * 1024 * 1024  # 1GB
            
            usage_percent = (avg_memory_bytes / allocated_memory_bytes) * 100
            return min(max(usage_percent, 0.0), 100.0)
            
        except Exception as e:
            logger.error(f"메모리 사용률 계산 오류: {e}")
            return 0.0
    
    async def _calculate_disk_usage(self, namespace: str) -> float:
        """디스크 사용률 계산 (PVC 기반)"""
        try:
            # PersistentVolumeClaim 목록 조회
            pvcs = self.v1.list_namespaced_persistent_volume_claim(namespace)
            
            if not pvcs.items:
                logger.debug("PVC가 없으므로 디스크 사용률을 0%로 설정")
                return 0.0
            
            total_usage = 0
            total_capacity = 0
            
            for pvc in pvcs.items:
                # PVC 용량 정보
                if pvc.status and pvc.status.capacity:
                    storage_str = pvc.status.capacity.get("storage", "0")
                    capacity_bytes = self._parse_storage_string(storage_str)
                    total_capacity += capacity_bytes
                    
                    # 실제 사용량은 별도 메트릭에서 가져와야 함
                    # 여기서는 임시로 50% 사용중으로 가정
                    total_usage += capacity_bytes * 0.5
            
            if total_capacity == 0:
                return 0.0
                
            usage_percent = (total_usage / total_capacity) * 100
            logger.debug(f"디스크 사용률: {usage_percent:.1f}%")
            
            return min(max(usage_percent, 0.0), 100.0)
            
        except Exception as e:
            logger.error(f"디스크 사용률 계산 오류: {e}")
            # 실패 시 적당한 값 반환 (모니터링용)
            return 45.0
    
    def _parse_memory_string(self, memory_str: str) -> int:
        """메모리 문자열을 바이트로 변환"""
        if memory_str.endswith('Ki'):
            return int(float(memory_str[:-2]) * 1024)
        elif memory_str.endswith('Mi'):
            return int(float(memory_str[:-2]) * 1024 * 1024)
        elif memory_str.endswith('Gi'):
            return int(float(memory_str[:-2]) * 1024 * 1024 * 1024)
        else:
            return int(float(memory_str))
    
    def _parse_storage_string(self, storage_str: str) -> int:
        """스토리지 문자열을 바이트로 변환"""
        if storage_str.endswith('Ki'):
            return int(float(storage_str[:-2]) * 1024)
        elif storage_str.endswith('Mi'):
            return int(float(storage_str[:-2]) * 1024 * 1024)
        elif storage_str.endswith('Gi'):
            return int(float(storage_str[:-2]) * 1024 * 1024 * 1024)
        elif storage_str.endswith('Ti'):
            return int(float(storage_str[:-2]) * 1024 * 1024 * 1024 * 1024)
        else:
            return int(float(storage_str))
    
    def _classify_pod_status(self, pod, sim_status: SimulationStatus, rosbag_status: str) -> str:
        """
        rosbag_status: "waiting" | "running" | "stopped" | None
        sim_status: 전체 시뮬레이션 상태
        """

        # 1️⃣ 시뮬레이션 STOPPED → rosbag stop
        if sim_status == SimulationStatus.STOPPED or rosbag_status == "stopped":
            return "stopped"

        # 2️⃣ rosbag play 중
        if rosbag_status == "running":
            return "running"

        # 3️⃣ rosbag 아직 play 안 됨
        if rosbag_status == "waiting":
            return "pending"

        # 4️⃣ Pod 종료 상태
        phase = pod.status.phase
        if phase == "Succeeded":
            return "success"
        elif phase in ["Failed", "CrashLoopBackOff", "Unknown"]:
            return "failed"

        # 기본 fallback
        return "pending"

    
    def _get_resource_status(self, usage_percent: float) -> str:
        """사용률에 따른 상태 결정"""
        if usage_percent >= 90:
            return "critical"
        elif usage_percent >= 70:
            return "warning"
        else:
            return "normal"
        
    def _get_preparing_resource_usage(self, reason: str) -> ResourceUsageData:
        """메트릭 준비 중 상태"""
        message_map = {
            "no_pods": "시뮬레이션 Pod를 생성하고 있습니다...",
            "pods_starting": "Pod가 시작 중입니다. 잠시만 기다려주세요...",
            "metrics_collecting": "메트릭을 수집하고 있습니다. 잠시만 기다려주세요..."
        }
        
        message = message_map.get(reason, "메트릭을 준비하고 있습니다...")
        logger.info(f"리소스 메트릭 준비 중: {reason}")
        
        return ResourceUsageData(
            cpu=ResourceUsage(usage_percent=0.0, status="collecting", message=message),
            memory=ResourceUsage(usage_percent=0.0, status="collecting", message=message),
            disk=ResourceUsage(usage_percent=0.0, status="collecting", message=message)
        )
    
    def _get_default_resource_usage(self, reason: str = "error") -> ResourceUsageData:
        """메트릭 수집 실패 시 기본값 - 에러 상태"""
        message_map = {
            "error": "리소스 메트릭을 가져올 수 없습니다. 잠시 후 다시 시도해주세요.",
            "timeout": "메트릭 조회 시간이 초과되었습니다.",
            "no_metrics_server": "Metrics Server가 설치되지 않았습니다. 관리자에게 문의하세요.",
            "permission_denied": "메트릭 조회 권한이 없습니다.",
            "api_error": "Kubernetes API 통신 중 오류가 발생했습니다."
        }
        
        message = message_map.get(reason, "메트릭 정보를 가져올 수 없습니다.")
        logger.warning(f"기본 리소스 사용률 데이터 사용: {reason}")
        
        return ResourceUsageData(
            cpu=ResourceUsage(usage_percent=-1.0, status="error", message=message),
            memory=ResourceUsage(usage_percent=-1.0, status="error", message=message),
            disk=ResourceUsage(usage_percent=-1.0, status="error", message=message)
        )
    
    def _get_resource_status_with_message(self, usage_percent: float) -> tuple[str, str]:
        """사용률에 따른 상태 및 메시지 결정 - normal만 반환"""
        if usage_percent >= 90:
            message = f"리소스 사용률이 매우 높습니다 ({usage_percent:.1f}%). 즉시 확인이 필요합니다."
        elif usage_percent >= 70:
            message = f"리소스 사용률이 높습니다 ({usage_percent:.1f}%). 모니터링을 권장합니다."
        else:
            message = f"정상 작동 중입니다 ({usage_percent:.1f}%)."
        
        return ("normal", message)
    
    def _get_default_pod_status(self) -> PodStatusData:
        """Pod 상태 수집 실패 시 기본값"""
        logger.warning("기본 Pod 상태 데이터 사용")
        return PodStatusData(
            total_count=0,
            overall_health_percent=0.0,
            status_breakdown={
                "success": StatusBreakdown(count=0, percentage=0.0),
                "pending": StatusBreakdown(count=0, percentage=0.0),
                "failed": StatusBreakdown(count=0, percentage=0.0)
            }
        )