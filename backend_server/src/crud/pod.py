from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, Union

import yaml
from fastapi import HTTPException
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from models.instance import Instance
from utils.my_enum import PodStatus

pod_client = None

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
    async def create_pod(instance: Union[Instance, Dict[str, Any]], template):
        """
        Pod를 생성하고 상세한 로깅을 제공하는 메서드
        
        Args:
            instance: Instance 객체 또는 instance 데이터 딕셔너리
            template: Template 객체
        
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
                instance_name = instance['name']
                simulation_id = instance['simulation_id']
                pod_namespace = instance['pod_namespace']
                template_id = instance.get('template_id')
                step_order = instance.get('step_order')
            else:
                # Instance 객체인 경우
                instance_id = instance.id
                instance_name = instance.name
                simulation_id = instance.simulation_id
                pod_namespace = instance.pod_namespace
                template_id = getattr(instance, 'template_id', None)
                step_order = getattr(instance, 'step_order', None)
        except KeyError as e:
            logging.error(f"❌ [Pod Creation] 필수 instance 데이터 누락: {e}")
            raise ValueError(f"Missing required instance data: {e}")
        
        pod_name = f"instance-{simulation_id}-{instance_id}"
        
        # 2. 시작 로그
        logging.info(f"🚀 [Pod Creation] 시작 - pod_name: {pod_name}")
        logging.info(f"📊 [Pod Creation] 파라미터 정보:")
        logging.info(f"   - instance_id: {instance_id}")
        logging.info(f"   - simulation_id: {simulation_id}")
        logging.info(f"   - template_type: {getattr(template, 'type', 'Unknown')}")
        logging.info(f"   - namespace: {pod_namespace}")
        logging.info(f"   - step_order: {step_order}")
        
        try:
            # 3. 기본 검증
            await PodService._validate_pod_creation_prerequisites(pod_namespace)
            
            # 4. 템플릿 로드 및 검증
            pod_spec = await PodService._load_and_validate_template()
            
            # 5. Pod 설정 및 메타데이터 구성
            configured_pod = PodService._configure_pod_metadata(
                pod_spec, pod_name, template, pod_namespace
            )
            
            # 6. 기존 Pod 중복 확인 및 처리
            await PodService._handle_existing_pod(pod_name, pod_namespace)
            
            # 7. Pod 생성 실행
            result = await PodService._create_pod_in_cluster(configured_pod, pod_namespace)
            
            # 8. 생성 후 검증
            await PodService._verify_pod_creation(pod_name, pod_namespace)
            
            logging.info(f"✅ [Pod Creation] Pod 생성 완료!")
            logging.info(f"   - Pod name: {pod_name}")
            logging.info(f"   - Namespace: {pod_namespace}")
            logging.info(f"   - UID: {result.metadata.uid}")
            
            return pod_name
            
        except Exception as e:
            logging.error(f"❌ [Pod Creation] 실패 - pod_name: {pod_name}")
            logging.error(f"   - Error: {type(e).__name__}: {e}")
            raise

    @staticmethod
    async def _validate_pod_creation_prerequisites(pod_namespace: str):
        """Pod 생성 전 기본 검증"""
        # pod_client 상태 확인
        if pod_client is None:
            raise ValueError("pod_client is not initialized")
        logging.debug("✅ [Pod Creation] pod_client 확인 완료")
        
        # 네임스페이스 존재 여부 확인
        try:
            pod_client.read_namespace(name=pod_namespace)
            logging.debug(f"✅ [Pod Creation] 네임스페이스 '{pod_namespace}' 존재 확인")
        except ApiException as e:
            if e.status == 404:
                logging.error(f"❌ [Pod Creation] 네임스페이스 '{pod_namespace}' 존재하지 않음")
                raise ValueError(f"Namespace '{pod_namespace}' does not exist")
            else:
                logging.error(f"❌ [Pod Creation] 네임스페이스 확인 실패: {e}")
                raise

    @staticmethod
    async def _load_and_validate_template() -> dict:
        """템플릿 파일 로드 및 검증"""
        template_path = "/robot-simulator/src/pod-template.yaml"
        
        if not os.path.exists(template_path):
            logging.error(f"❌ [Pod Creation] 템플릿 파일 없음: {template_path}")
            raise FileNotFoundError(f"Template file not found: {template_path}")
        
        logging.debug(f"✅ [Pod Creation] 템플릿 파일 존재 확인: {template_path}")
        
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                pod_spec = yaml.safe_load(f)
            logging.debug("✅ [Pod Creation] 템플릿 파일 읽기 완료")
        except yaml.YAMLError as e:
            logging.error(f"❌ [Pod Creation] YAML 파싱 에러: {e}")
            raise
        except Exception as e:
            logging.error(f"❌ [Pod Creation] 템플릿 파일 읽기 실패: {e}")
            raise
        
        # 템플릿 구조 검증
        if not pod_spec or 'metadata' not in pod_spec or 'spec' not in pod_spec:
            raise ValueError("Invalid pod template format - missing metadata or spec")
        
        if 'containers' not in pod_spec['spec'] or not pod_spec['spec']['containers']:
            raise ValueError("No containers found in pod template")
        
        logging.debug(f"✅ [Pod Creation] 템플릿 검증 완료 - 컨테이너 수: {len(pod_spec['spec']['containers'])}")
        
        return pod_spec

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
        
        logging.debug(f"📝 [Pod Creation] 메타데이터 설정 완료")
        logging.debug(f"   - name: {pod_name}")
        logging.debug(f"   - labels: {pod_label}")
        logging.debug(f"   - namespace: {pod_namespace}")
        
        return configured_pod

    @staticmethod
    async def _handle_existing_pod(pod_name: str, pod_namespace: str):
        """기존 Pod 중복 확인 및 처리"""
        try:
            existing_pod = pod_client.read_namespaced_pod(
                name=pod_name, 
                namespace=pod_namespace
            )
            logging.warning(f"⚠️ [Pod Creation] 같은 이름의 Pod가 이미 존재: {pod_name}")
            logging.warning(f"   - 상태: {existing_pod.status.phase}")
            
            # 정책에 따라 처리 (현재는 경고만)
            # 필요시 기존 Pod 삭제 또는 다른 이름 사용 로직 추가
            
        except ApiException as e:
            if e.status == 404:
                logging.debug("✅ [Pod Creation] 중복 Pod 없음 - 생성 진행")
            else:
                logging.warning(f"⚠️ [Pod Creation] Pod 중복 확인 실패: {e}")
                # 중복 확인 실패는 치명적이지 않으므로 계속 진행

    @staticmethod
    async def _create_pod_in_cluster(configured_pod: dict, pod_namespace: str):
        """실제 클러스터에 Pod 생성"""
        try:
            logging.info(f"🏗️ [Pod Creation] Pod 생성 실행 중...")
            
            result = pod_client.create_namespaced_pod(
                namespace=pod_namespace, 
                body=configured_pod
            )
            
            logging.info(f"✅ [Pod Creation] Kubernetes API 호출 성공")
            return result
            
        except ApiException as e:
            logging.error(f"❌ [Pod Creation] Kubernetes API 에러:")
            logging.error(f"   - Status: {e.status}")
            logging.error(f"   - Reason: {e.reason}")
            logging.error(f"   - Body: {e.body}")
            raise
        except Exception as e:
            logging.error(f"❌ [Pod Creation] 예상치 못한 에러: {type(e).__name__}: {e}")
            import traceback
            logging.error(f"❌ [Pod Creation] 스택 트레이스:\n{traceback.format_exc()}")
            raise

    @staticmethod
    async def _verify_pod_creation(pod_name: str, pod_namespace: str):
        """생성된 Pod 상태 검증"""
        try:
            created_pod = pod_client.read_namespaced_pod(
                name=pod_name, 
                namespace=pod_namespace
            )
            logging.info(f"🔍 [Pod Creation] 생성된 Pod 상태: {created_pod.status.phase}")
            
            # 추가 상태 정보
            if created_pod.status.conditions:
                for condition in created_pod.status.conditions:
                    if condition.status == "True":
                        logging.debug(f"   - {condition.type}: {condition.status}")
            
        except Exception as e:
            logging.warning(f"⚠️ [Pod Creation] 생성된 Pod 상태 확인 실패: {e}")
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
                print(f"네임스페이스 '{name}' 생성 성공")
                return name
            else:
                raise Exception("네임스페이스 생성 결과가 None")
                
        except Exception as e:
            print(f"네임스페이스 '{name}' 생성 실패: {e}")
            raise Exception(f"네임스페이스 생성 실패: {str(e)}")

    @staticmethod
    async def delete_namespace(simulation_id: int):
        name = f"simulation-{simulation_id}"
        try:
            pod_client.delete_namespace(name=name)
            print(f"네임스페이스 '{name}' 삭제 성공")
        except Exception as e:
            print(f"네임스페이스 '{name}' 삭제 실패: {e}")
            # 삭제는 실패해도 크리티컬하지 않을 수 있음
            pass

    @staticmethod
    async def get_pod_ip(instance: Instance):
        pod = pod_client.read_namespaced_pod(name=instance.pod_name, namespace=instance.pod_namespace)
        return pod.status.pod_ip

    async def is_pod_ready(self, instance: Instance):
        pod_status = await self.get_pod_status(instance.pod_name, instance.pod_namespace)
        return pod_status == PodStatus.RUNNING.value

    async def check_pod_status(self, instance: Instance):
        pod_status = await self.get_pod_status(instance.pod_name, instance.pod_namespace)
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
