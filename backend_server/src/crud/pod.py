import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Union

import yaml
from fastapi import HTTPException
from kubernetes import client, config
from kubernetes.client import V1Pod
from kubernetes.client.rest import ApiException

from utils.debug_print import debug_print
from schemas.pod import GroupIdFilter, StepOrderFilter
from models.instance import Instance
from utils.my_enum import PodStatus

pod_client = None

logger = logging.getLogger(__name__)

# config.load_kube_config('/root/.kube/config')  # 로컬 실행 시에는 주석 처리 필수
try:
    config.load_kube_config()
    pod_client = client.CoreV1Api()
    print("✅ kubeconfig 로드 성공!")
except Exception as e:
    print(f"❌ kubeconfig 로드 실패: {e}")
    try:
        config.load_incluster_config()
        pod_client = client.CoreV1Api()
        print("✅ incluster config 로드 성공!")
    except Exception as e2:
        print(f"❌ 모든 Kubernetes 설정 로드 실패: {e2}")
        pod_client = None

class PodService:
    @staticmethod
    async def create_pod(instance: Union[Instance, Dict[str, Any]], template, simulation_config: Dict[str, Any] = None):
        """
        Pod를 생성하고 상세한 로깅을 제공하는 메서드
        
        Args:
            instance: Instance 객체 또는 instance 데이터 딕셔너리
            template: Template 객체
            simulation_config: 시뮬레이션 설정 정보 (bag 파일 경로, 반복 횟수 등)
        
        Returns:
            str: 생성된 Pod 이름
        
        Raises:
            ValueError: 잘못된 파라미터나 설정
            FileNotFoundError: 템플릿 파일을 찾을 수 없음
            ApiException: Kubernetes API 에러
        """
        
        # 1. Instance 데이터 추출 및 정규화
        try:
            if isinstance(instance, dict):
                instance_id = instance['id']
                simulation_id = instance['simulation_id']
                pod_namespace = instance['pod_namespace']
                template_id = instance.get('template_id')
                step_order = instance.get('step_order')
            else:
                # Instance 객체인 경우
                instance_id = instance.id
                simulation_id = instance.simulation_id
                pod_namespace = instance.pod_namespace
                template_id = getattr(instance, 'template_id', None)
                step_order = getattr(instance, 'step_order', None)
        except KeyError as e:
            logger.error(f"❌ [Pod Creation] 필수 instance 데이터 누락: {e}")
            raise ValueError(f"Missing required instance data: {e}")
        
        # 실행 패턴 정보 추출
        pattern_type = simulation_config.get("pattern_type", "sequential")
        group_id = instance.get('group_id') if isinstance(instance, dict) else getattr(instance, 'group_id', None)
        
        if pattern_type == "sequential":
            # 순차 실행: step 순서 포함
            pod_name = f"sim-{simulation_id}-step-{step_order or 0}-instance-{instance_id}"
        else:
            # 병렬 실행: group ID 포함 (group_id가 없으면 기본 그룹)
            group_identifier = group_id if group_id is not None else "default"
            pod_name = f"sim-{simulation_id}-group-{group_identifier}-instance-{instance_id}"
        
        # 2. 시작 로그
        logger.info(f"🚀 [Pod Creation] 시작 - pod_name: {pod_name}")
        logger.info(f"📊 [Pod Creation] 파라미터 정보:")
        logger.info(f"   - instance_id: {instance_id}")
        logger.info(f"   - simulation_id: {simulation_id}")
        logger.info(f"   - pattern_type: {pattern_type}")
        logger.info(f"   - step_order: {step_order}")
        logger.info(f"   - group_id: {group_id}")
        logger.info(f"   - template_type: {getattr(template, 'type', 'Unknown')}")
        logger.info(f"   - namespace: {pod_namespace}")
        
        try:
            # 3. 기본 검증
            await PodService._validate_pod_creation_prerequisites(pod_namespace)
            
            # 4. 템플릿 로드 및 검증
            pod_spec = await PodService._load_and_validate_template()
            
            # 5. Pod 설정 및 메타데이터 구성
            configured_pod = PodService._configure_pod_metadata_enhanced(
                pod_spec, pod_name, template, pod_namespace,
                instance_id, simulation_id, step_order, 
                simulation_config, pattern_type, group_id
            )
            
            # 6. 기존 Pod 중복 확인 및 처리
            await PodService._handle_existing_pod(pod_name, pod_namespace)
            
            # 7. Pod 생성 실행
            result = await PodService._create_pod_in_cluster(configured_pod, pod_namespace)
            
            # 8. 생성 후 검증
            await PodService._verify_pod_creation(pod_name, pod_namespace)
            
            logger.info(f"✅ [Pod Creation] Pod 생성 완료!")
            logger.info(f"   - Pod name: {pod_name}")
            logger.info(f"   - Namespace: {pod_namespace}")
            logger.info(f"   - UID: {result.metadata.uid}")
            
            return pod_name
            
        except Exception as e:
            logger.error(f"❌ [Pod Creation] 실패 - pod_name: {pod_name}")
            logger.error(f"   - Error: {type(e).__name__}: {e}")
            raise

    @staticmethod
    async def _validate_pod_creation_prerequisites(pod_namespace: str):
        """Pod 생성 전 기본 검증"""
        # pod_client 상태 확인
        if pod_client is None:
            raise ValueError("pod_client is not initialized")
        logger.debug("✅ [Pod Creation] pod_client 확인 완료")
        
        # 네임스페이스 존재 여부 확인
        try:
            pod_client.read_namespace(name=pod_namespace)
            logger.debug(f"✅ [Pod Creation] 네임스페이스 '{pod_namespace}' 존재 확인")
        except ApiException as e:
            if e.status == 404:
                logger.error(f"❌ [Pod Creation] 네임스페이스 '{pod_namespace}' 존재하지 않음")
                raise ValueError(f"Namespace '{pod_namespace}' does not exist")
            else:
                logger.error(f"❌ [Pod Creation] 네임스페이스 확인 실패: {e}")
                raise

    @staticmethod
    async def _load_and_validate_template() -> dict:
        """템플릿 파일 로드 및 검증"""
        template_path = "/robot-simulator/src/pod-template.yaml"
        
        if not os.path.exists(template_path):
            logger.error(f"❌ [Pod Creation] 템플릿 파일 없음: {template_path}")
            raise FileNotFoundError(f"Template file not found: {template_path}")
        
        logger.debug(f"✅ [Pod Creation] 템플릿 파일 존재 확인: {template_path}")
        
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                pod_spec = yaml.safe_load(f)
            logger.debug("✅ [Pod Creation] 템플릿 파일 읽기 완료")
        except yaml.YAMLError as e:
            logger.error(f"❌ [Pod Creation] YAML 파싱 에러: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ [Pod Creation] 템플릿 파일 읽기 실패: {e}")
            raise
        
        # 템플릿 구조 검증
        if not pod_spec or 'metadata' not in pod_spec or 'spec' not in pod_spec:
            raise ValueError("Invalid pod template format - missing metadata or spec")
        
        if 'containers' not in pod_spec['spec'] or not pod_spec['spec']['containers']:
            raise ValueError("No containers found in pod template")
        
        logger.debug(f"✅ [Pod Creation] 템플릿 검증 완료 - 컨테이너 수: {len(pod_spec['spec']['containers'])}")
        
        return pod_spec
    
    # 개선된 Pod 메타데이터 구성
    @staticmethod
    def _configure_pod_metadata_enhanced(
        pod_spec: dict, 
        pod_name: str, 
        template, 
        pod_namespace: str,
        instance_id: str,
        simulation_id: str,
        step_order: int = None,
        simulation_config: Dict[str, Any] = None,
        pattern_type: str = 'parallel',
        group_id: str = None
    ) -> dict:
        """Pod 메타데이터 및 설정 구성 (시뮬레이션 모니터링 강화)"""
        import copy
        import datetime
        
        configured_pod = copy.deepcopy(pod_spec)
        
        # 현재 시간 (UTC)
        creation_time = datetime.datetime.utcnow().isoformat() + "Z"
        
        # 시뮬레이션 설정 기본값
        simulation_config = simulation_config or {}
        bag_file_path = simulation_config.get('bag_file_path', '')
        repeat_count = simulation_config.get('repeat_count', 1)
        max_execution_time = simulation_config.get('max_execution_time', '3600s')
        
        # 확장된 Labels - 모니터링 도구가 쉽게 식별할 수 있도록
        pod_labels = {
            # 기본 식별 정보
            "app": "simulation-pod",
            "component": "simulation-instance",
            "part-of": "simulation-platform",
            
            # 시뮬레이션 관련 정보
            "simulation-id": str(simulation_id),
            "instance-id": str(instance_id),
            
            # 실행 패턴 및 그룹핑 정보
            "pattern-type": pattern_type,
            "step-order": str(step_order) if step_order is not None else "0",
            "group-id": str(group_id) if group_id is not None else "default",
            
            # 템플릿 및 에이전트 타입
            "agent-type": getattr(template, 'type', 'unknown').lower(),
            "template-id": str(getattr(template, 'template_id', 'unknown')),
            
            # 실행 상태
            "execution-phase": "initialization",
            
            # 모니터링 레이블
            "monitoring-enabled": "true",
            "resource-tracking": "enabled",
            "log-aggregation": "enabled"
        }
        
        # Annotations - 상세 메타데이터 및 시뮬레이션 설정
        pod_annotations = {
            # 생성 정보
            "simulation-platform/created-at": creation_time,
            "simulation-platform/created-by": "pod-service",
            
            # 시뮬레이션 상세 정보
            "simulation-platform/simulation-id": str(simulation_id),
            "simulation-platform/instance-id": str(instance_id),
            
            # 실행 패턴 상세 정보
            "simulation-platform/pattern-type": pattern_type,
            "simulation-platform/step-order": str(step_order) if step_order is not None else "0",
            "simulation-platform/group-id": str(group_id) if group_id is not None else "default",
            "simulation-platform/execution-dependency": "none" if pattern_type == 'parallel' else f"step-{step_order - 1}" if step_order and step_order > 0 else "none",
            
            # Bag 파일 및 실행 설정
            "simulation-platform/bag-file-path": bag_file_path,
            "simulation-platform/repeat-count": str(repeat_count),
            "simulation-platform/max-execution-time": max_execution_time,
            "simulation-platform/current-iteration": "0",
            
            # 모니터링 설정
            "simulation-platform/monitoring-endpoint": f"/metrics/{simulation_id}/{pattern_type}/{instance_id}",
            "simulation-platform/log-stream": f"simulation-{simulation_id}-{pattern_type}-instance-{instance_id}",
            "simulation-platform/status-endpoint": f"/status/{simulation_id}/{pattern_type}/{instance_id}",
            
            # 리소스 관리
            "simulation-platform/resource-group": f"simulation-{simulation_id}-{pattern_type}",
            "simulation-platform/cleanup-policy": "auto",
            "simulation-platform/backup-enabled": "true",
            
            # 네트워크 및 통신
            "simulation-platform/communication-port": str(simulation_config.get('communication_port', 11311)),
            "simulation-platform/data-exchange-format": simulation_config.get('data_format', 'ros-bag'),
            
            # 디버깅 및 개발
            "simulation-platform/debug-mode": str(simulation_config.get('debug_mode', False)).lower(),
            "simulation-platform/log-level": simulation_config.get('log_level', 'INFO'),
            
            # 템플릿 상세 정보
            "simulation-platform/template-type": getattr(template, 'type', 'unknown'),
            "simulation-platform/template-description": getattr(template, 'description', '')
        }
        
        # 메타데이터 적용
        configured_pod["metadata"]["name"] = pod_name
        configured_pod["metadata"]["labels"] = pod_labels
        configured_pod["metadata"]["annotations"] = pod_annotations
        configured_pod["metadata"]["namespace"] = pod_namespace
        
        # 컨테이너 설정
        if configured_pod["spec"]["containers"]:
            container = configured_pod["spec"]["containers"][0]
            container["name"] = pod_name
            
            # 환경 변수 추가 - 컨테이너 내에서 시뮬레이션 정보 접근
            if "env" not in container:
                container["env"] = []
            
            simulation_env_vars = [
                {"name": "SIMULATION_ID", "value": str(simulation_id)},
                {"name": "INSTANCE_ID", "value": str(instance_id)},
                {"name": "PATTERN_TYPE", "value": pattern_type},
                {"name": "STEP_ORDER", "value": str(step_order) if step_order is not None else "0"},
                {"name": "GROUP_ID", "value": str(group_id) if group_id is not None else "default"},
                {"name": "AGENT_TYPE", "value": getattr(template, 'type', 'unknown')},
                {"name": "BAG_FILE_PATH", "value": bag_file_path},
                {"name": "REPEAT_COUNT", "value": str(repeat_count)},
                {"name": "POD_NAME", "value": pod_name},
                {"name": "POD_NAMESPACE", "value": pod_namespace},
                {"name": "DEBUG_MODE", "value": str(simulation_config.get('debug_mode', False)).lower()},
                {"name": "LOG_LEVEL", "value": simulation_config.get('log_level', 'INFO')}
            ]
            
            container["env"].extend(simulation_env_vars)
        
        logger.debug(f"📝 [Pod Creation] 개선된 메타데이터 설정 완료")
        logger.debug(f"   - name: {pod_name}")
        logger.debug(f"   - simulation-id: {simulation_id}")
        logger.debug(f"   - pattern-type: {pattern_type}")
        logger.debug(f"   - step-order: {step_order}")
        logger.debug(f"   - group-id: {group_id}")
        logger.debug(f"   - labels count: {len(pod_labels)}")
        logger.debug(f"   - annotations count: {len(pod_annotations)}")
        logger.debug(f"   - bag-file-path: {bag_file_path}")
        logger.debug(f"   - repeat-count: {repeat_count}")
        
        return configured_pod

    @staticmethod
    def _configure_pod_metadata(pod_spec: dict, pod_name: str, template, pod_namespace: str) -> dict:
        """Pod 메타데이터 및 설정 구성"""
        # 딥 카피로 원본 템플릿 보호
        import copy
        configured_pod = copy.deepcopy(pod_spec)
        
        # 메타데이터 설정
        pod_label = {"agent-type": getattr(template, 'type', 'unknown').lower()}
        
        configured_pod["metadata"]["name"] = pod_name
        configured_pod["metadata"]["labels"] = pod_label
        configured_pod["metadata"]["namespace"] = pod_namespace
        
        # 컨테이너 이름 설정
        if configured_pod["spec"]["containers"]:
            configured_pod["spec"]["containers"][0]["name"] = pod_name
        
        logger.debug(f"📝 [Pod Creation] 메타데이터 설정 완료")
        logger.debug(f"   - name: {pod_name}")
        logger.debug(f"   - labels: {pod_label}")
        logger.debug(f"   - namespace: {pod_namespace}")
        
        return configured_pod

    @staticmethod
    async def _handle_existing_pod(pod_name: str, pod_namespace: str):
        """기존 Pod 중복 확인 및 처리"""
        try:
            existing_pod = pod_client.read_namespaced_pod(
                name=pod_name, 
                namespace=pod_namespace
            )
            logger.warning(f"⚠️ [Pod Creation] 같은 이름의 Pod가 이미 존재: {pod_name}")
            logger.warning(f"   - 상태: {existing_pod.status.phase}")
            
            # 정책에 따라 처리 (현재는 경고만)
            # 필요시 기존 Pod 삭제 또는 다른 이름 사용 로직 추가
            
        except ApiException as e:
            if e.status == 404:
                logger.debug("✅ [Pod Creation] 중복 Pod 없음 - 생성 진행")
            else:
                logger.warning(f"⚠️ [Pod Creation] Pod 중복 확인 실패: {e}")
                # 중복 확인 실패는 치명적이지 않으므로 계속 진행

    @staticmethod
    async def _create_pod_in_cluster(configured_pod: dict, pod_namespace: str):
        """실제 클러스터에 Pod 생성"""
        try:
            logger.info(f"🏗️ [Pod Creation] Pod 생성 실행 중...")
            
            result = pod_client.create_namespaced_pod(
                namespace=pod_namespace, 
                body=configured_pod
            )
            
            logger.info(f"✅ [Pod Creation] Kubernetes API 호출 성공")
            return result
            
        except ApiException as e:
            logger.error(f"❌ [Pod Creation] Kubernetes API 에러:")
            logger.error(f"   - Status: {e.status}")
            logger.error(f"   - Reason: {e.reason}")
            logger.error(f"   - Body: {e.body}")
            raise
        except Exception as e:
            logger.error(f"❌ [Pod Creation] 예상치 못한 에러: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"❌ [Pod Creation] 스택 트레이스:\n{traceback.format_exc()}")
            raise

    @staticmethod
    async def _verify_pod_creation(pod_name: str, pod_namespace: str):
        """생성된 Pod 상태 검증"""
        try:
            created_pod = pod_client.read_namespaced_pod(
                name=pod_name, 
                namespace=pod_namespace
            )
            logger.info(f"🔍 [Pod Creation] 생성된 Pod 상태: {created_pod.status.phase}")
            
            # 추가 상태 정보
            if created_pod.status.conditions:
                for condition in created_pod.status.conditions:
                    if condition.status == "True":
                        logger.debug(f"   - {condition.type}: {condition.status}")
            
        except Exception as e:
            logger.warning(f"⚠️ [Pod Creation] 생성된 Pod 상태 확인 실패: {e}")
            # 상태 확인 실패는 치명적이지 않으므로 무시
            
    @staticmethod
    async def get_pod_status(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        return pod.status.phase

    @staticmethod
    async def get_pod_image(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        return pod.spec.containers[0].image if pod.spec.containers else ""

    @staticmethod
    async def get_pod_age(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        creation_time = pod.metadata.creation_timestamp
        time_difference = datetime.now(timezone.utc) - creation_time

        total_seconds = int(time_difference.total_seconds())
        days, remainder = divmod(total_seconds, 86400)  # 1일 = 86400초
        hours, remainder = divmod(remainder, 3600)  # 1시간 = 3600초
        minutes, seconds = divmod(remainder, 60)  # 1분 = 60초

        time_units = [("d", days), ("h", hours), ("m", minutes), ("s", seconds)]
        return next(f"{value}{unit}" for unit, value in time_units if value > 0)

    @staticmethod
    async def get_pod_label(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        if pod.metadata.labels:
            label = next(iter(pod.metadata.labels.items()))
            return str(label[1])
        return ""

    @staticmethod
    async def delete_pod(pod_name, namespace):
        pod_client.delete_namespaced_pod(name=pod_name, namespace=namespace)

    @staticmethod
    async def create_namespace(simulation_id: int):
        name = f"simulation-{simulation_id}"
        try:
            metadata = client.V1ObjectMeta(name=name)
            namespace = client.V1Namespace(metadata=metadata)
            
            # 생성 시도
            result = pod_client.create_namespace(namespace)
            
            # 생성 확인
            if result:
                logger.info(f"네임스페이스 '{name}' 생성 성공")
                return name
            else:
                raise Exception("네임스페이스 생성 결과가 None")
                
        except Exception as e:
            logger.info(f"네임스페이스 '{name}' 생성 실패: {e}")
            raise Exception(f"네임스페이스 생성 실패: {str(e)}")

    @staticmethod
    async def wait_namespace_deleted(name: str, timeout: int = 60, interval: float = 1.0):
        """
        네임스페이스 삭제 완료까지 대기 (주기적 상태 로깅 포함)
        :param name: 네임스페이스 이름
        :param timeout: 최대 대기 시간(초)
        :param interval: 상태 체크 간격(초)
        """
        waited = 0
        while waited < timeout:
            try:
                pod_client.read_namespace(name=name)
                # 존재하면 아직 삭제 중
                debug_print(f"네임스페이스 '{name}' 삭제 진행 중... {waited}/{timeout}초 경과")
            except ApiException as e:
                if e.status == 404:
                    debug_print(f"네임스페이스 '{name}' 완전히 삭제됨")
                    return True
                else:
                    debug_print(f"네임스페이스 '{name}' 조회 실패: {e}")
                    raise
            await asyncio.sleep(interval)
            waited += interval
        raise TimeoutError(f"네임스페이스 '{name}' 삭제가 {timeout}초 내 완료되지 않음")

    @staticmethod
    async def delete_namespace(simulation_id: int):
        name = f"simulation-{simulation_id}"
        try:
            pod_client.delete_namespace(name=name)
            debug_print(f"네임스페이스 '{name}' 삭제 요청 성공, 완료 대기 중...")
            await PodService.wait_namespace_deleted(name)
            debug_print(f"네임스페이스 '{name}' 삭제 완료")
        except ApiException as e:
            if e.status == 404:
                # 이미 삭제된 경우
                debug_print(f"네임스페이스 '{name}' 이미 없음")
            else:
                debug_print(f"네임스페이스 '{name}' 삭제 실패: {e}")
        except Exception as e:
            debug_print(f"네임스페이스 '{name}' 삭제 중 알 수 없는 오류 발생: {e}")

    @staticmethod
    async def get_pod_ip(instance: Instance):
        pod = pod_client.read_namespaced_pod(name=instance.pod_name, namespace=instance.pod_namespace)
        return pod.status.pod_ip
    
    @staticmethod
    def get_v1pod_ip(pod: V1Pod) -> str:
        """Pod 객체에서 IP 반환"""
        return pod.status.pod_ip or "unknown"
    

    async def is_pod_ready(self, instance: Instance):
        pod_status = await self.get_pod_status(instance.pod_name, instance.pod_namespace)
        return pod_status == PodStatus.RUNNING.value

    async def check_pod_status(self, instance: Instance):
        pod_status = await self.get_pod_status(instance.pod_name, instance.pod_namespace)
        code = await self.get_pod_status_code(pod_status)
        if code != 200:
            raise HTTPException(status_code=code, detail=f"Pod Status: {pod_status}")
        
    async def check_v1pod_status(self, pod: V1Pod):
        pod_status = pod.status.phase
        code = await self.get_pod_status_code(pod_status)
        if code != 200:
            raise HTTPException(status_code=code, detail=f"Pod Status: {pod_status}")

    @staticmethod
    async def get_pod_status_code(pod_status):
        """Pod 상태에 따른 상태 코드 반환"""
        status_code_map = {
            "Pending": 520,
            "ContainerCreating": 521,
            "Running": 200,
            "Error": 500,
            "ImagePullBackOff": 522,
            "ErrImagePull": 523,
            "CrashLoopBackOff": 524,
            "Unknown": 530,
        }
        return status_code_map.get(pod_status)
    
    def get_pods_by_filter(
        self, 
        namespace: str, 
        filter_params: Union[StepOrderFilter, GroupIdFilter]
    ) -> List[V1Pod]:
        """
        패턴에 따라 다른 label 필드로 pod 목록을 조회
        
        Args:
            namespace: Kubernetes 네임스페이스
            filter_params: step_order 또는 group_id를 포함한 필터 파라미터
            
        Returns:
            List[V1Pod]: 필터링된 pod 목록
        """
        # 필터 타입에 따라 label selector 생성
        if "step_order" in filter_params:
            label_selector = f"step-order={filter_params['step_order']}"
        elif "group_id" in filter_params:
            label_selector = f"group-id={filter_params['group_id']}"
        else:
            raise ValueError("Invalid filter parameters. Must contain either 'step_order' or 'group_id'")
        
        # Kubernetes API를 통해 pod 목록 조회
        try:
            pod_list = pod_client.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
                _request_timeout=30
            )
            return pod_list.items
        except Exception as e:
            raise RuntimeError(f"Failed to get pods: {str(e)}")
    
    async def get_pods_by_step_order(self, namespace: str, step_order: int) -> List[V1Pod]:
        """
        step_order로 pod 목록 조회 (편의 메서드)
        """
        return await self.get_pods_by_filter(
            namespace, 
            {"step_order": step_order}
        )
    
    async def get_pods_by_group_id(self, namespace: str, group_id: int) -> List[V1Pod]:
        """
        group_id로 pod 목록 조회 (편의 메서드)
        """
        return await self.get_pods_by_filter(
            namespace, 
            {"group_id": group_id}
        )
