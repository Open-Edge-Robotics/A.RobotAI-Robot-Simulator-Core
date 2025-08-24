from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from kubernetes.client import V1Pod

from utils.debug_print import debug_print
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.simulation_groups import SimulationGroup

logger = logging.getLogger(__name__)

class ExecutionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"

@dataclass
class PodExecutionResult:
    pod_name: str
    pod_ip: str
    status: ExecutionStatus
    message: str
    response_data: Dict = None
    execution_time: float = 0.0

class RosbagExecutor:
    DEFAULT_TIMEOUT = 300
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    def __init__(self, pod_service, ros_service):
        self.pod_service = pod_service
        #self.pod_service.enabled = False
        self.ros_service = ros_service
        
    
    async def execute_rosbag_on_pod_direct(self, pod: V1Pod, simulation, step=None, group=None) -> PodExecutionResult:
        """
        단일 Pod에서 rosbag 실행
        step 또는 group 중 하나가 제공되어야 함
        """
        start_time = datetime.now(timezone.utc)
        pod_name = pod.metadata.name
        
        try:
            print(f"Pod {pod_name}에서 rosbag 실행 시작")
            
            bag_file_path = self._extract_bag_file_path_from_pod(pod)
            if not bag_file_path:
                raise ValueError(f"Pod {pod_name}에 BAG_FILE_PATH 환경변수가 없음")
            
            print(f"Pod {pod_name} bag_file_path: {bag_file_path}")
            
            if pod.status.phase != "Running":
                raise ValueError(f"Pod {pod_name} 상태가 Running이 아님: {pod.status.phase}")
            
            pod_ip = pod.status.pod_ip
            if not pod_ip:
                raise ValueError(f"Pod {pod_name}에 IP가 할당되지 않음")
            
            print(f"Pod {pod_name} IP: {pod_ip}")
            
            # step 또는 group에 따른 파라미터 준비
            rosbag_params = self._prepare_rosbag_params_from_pod(
                pod, bag_file_path, simulation, step=step, group=group
            )
            print(f"Pod {pod_name} rosbag 파라미터: {rosbag_params}")
            
            response = await self._execute_with_retry(pod_ip, rosbag_params, pod_name)
            
            execution_time = datetime.now(timezone.utc) - start_time
            
            # 초 단위 float 변환
            execution_seconds = execution_time.total_seconds()
            logger.info(f"Pod {pod_name} rosbag 실행 성공 (소요시간: {execution_seconds}초)")
            
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod_ip,
                status=ExecutionStatus.SUCCESS,
                message="Rosbag 실행 성공",
                response_data=response,
                execution_time=execution_seconds
            )
            
        except asyncio.TimeoutError:
            execution_time = datetime.now(timezone.utc) - start_time
            execution_seconds = execution_time.total_seconds()
            error_msg = f"Pod {pod_name} rosbag 실행 타임아웃 ({self.DEFAULT_TIMEOUT}초)"
            logger.error(error_msg)
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod.status.pod_ip or "unknown",
                status=ExecutionStatus.TIMEOUT,
                message=error_msg,
                execution_time=execution_seconds
            )
            
        except Exception as e:
            execution_time = datetime.now(timezone.utc) - start_time
            execution_seconds = execution_time.total_seconds()
            error_msg = f"Pod {pod_name} rosbag 실행 실패: {str(e)}"
            logger.error(error_msg)
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod.status.pod_ip or "unknown",
                status=ExecutionStatus.FAILED,
                message=error_msg,
                execution_time=execution_seconds
            )
    
    def _extract_bag_file_path_from_pod(self, pod: V1Pod) -> str:
        if not pod.spec.containers:
            return None
            
        container = pod.spec.containers[0]
        if not container.env:
            return None
            
        for env_var in container.env:
            if env_var.name == "BAG_FILE_PATH":
                return env_var.value
                
        return None
    
    def _get_pod_env_value(self, pod: V1Pod, env_name: str, default_value=None):
        if not pod.spec.containers or not pod.spec.containers[0].env:
            return default_value
            
        for env_var in pod.spec.containers[0].env:
            if env_var.name == env_name:
                return env_var.value
                
        return default_value
    
    def _prepare_rosbag_params_from_pod(self, pod: V1Pod, bag_file_path: str, simulation: Simulation, 
                                      step: Optional[SimulationStep] = None, 
                                      group: Optional[SimulationGroup] = None) -> Dict[str, Any]:
        """
        Pod에서 rosbag 파라미터 준비
        step 또는 group 중 하나가 제공되어야 함
        """
        if step is None and group is None:
            raise ValueError("step 또는 group 중 하나는 반드시 제공되어야 합니다")
        
        pod_repeat_count = self._get_pod_env_value(pod, "REPEAT_COUNT")
        pod_execution_time = self._get_pod_env_value(pod, "EXECUTION_TIME")
        
        # step이 제공된 경우 (순차 패턴)
        if step is not None:
            params = {
                "object_path": bag_file_path,
                "max_loops": (
                    step.repeat_count or 
                    (int(pod_repeat_count) if pod_repeat_count else None) or 
                    simulation.repeat_count or 1
                ),
                "delay_between_loops": step.delay_after_completion or 0,
                "execution_duration": (
                    step.execution_time or 
                    (int(pod_execution_time) if pod_execution_time else None) or 
                    simulation.execution_time or 0
                ),
            }
        # group이 제공된 경우 (병렬 패턴)
        else:
            params = {
                "object_path": bag_file_path,
                "max_loops": (
                    getattr(group, 'repeat_count', None) or
                    (int(pod_repeat_count) if pod_repeat_count else None) or 
                    simulation.repeat_count or 1
                ),
                "delay_between_loops": getattr(group, 'delay_after_completion', None) or 0,
                "execution_duration": (
                    getattr(group, 'execution_time', None) or
                    (int(pod_execution_time) if pod_execution_time else None) or 
                    simulation.execution_time or 0
                ),
            }
        
        if params["max_loops"] <= 0:
            raise ValueError(f"max_loops는 1 이상이어야 합니다: {params['max_loops']}")
        if params["delay_between_loops"] < 0:
            raise ValueError(f"delay_between_loops는 0 이상이어야 합니다: {params['delay_between_loops']}")
        if params["execution_duration"] < 0:
            raise ValueError(f"execution_duration는 0 이상이어야 합니다: {params['execution_duration']}")
        
        return params
    
    async def _execute_with_retry(self, pod_ip: str, rosbag_params: Dict, pod_name: str):
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await asyncio.wait_for(
                    self.ros_service.send_post_request(pod_ip, "/rosbag/play", rosbag_params),
                    timeout=self.DEFAULT_TIMEOUT
                )
                
                logger.debug(f"Pod {pod_name} rosbag 실행 응답: {response}")
                return response
                
            except asyncio.TimeoutError:
                logger.warning(f"Pod {pod_name} rosbag 실행 타임아웃 (시도 {attempt + 1}/{self.MAX_RETRIES})")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    raise
                    
            except Exception as e:
                logger.warning(f"Pod {pod_name} rosbag 실행 실패 (시도 {attempt + 1}/{self.MAX_RETRIES}): {str(e)}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    raise
    
    async def execute_rosbag_parallel_pods(self, pods: List[V1Pod], simulation, 
                                         step: Optional[SimulationStep] = None, 
                                         group: Optional[SimulationGroup] = None) -> List[PodExecutionResult]:
        """
        여러 Pod에서 병렬 rosbag 실행
        step 또는 group 중 하나가 제공되어야 함
        """
        context_type = "스텝" if step else "그룹"
        context_id = step.step_order if step else group.id
        debug_print(f"🎯 RosbagExecutor 병렬 실행 시작", context_type=context_type, context_id=context_id, pod_count=len(pods))
        
        if not pods:
            debug_print("⚠️ 실행할 Pod 없음", context_type=context_type, context_id=context_id)
            return []

        if step is None and group is None:
            debug_print("❌ step과 group 모두 None", context_type=context_type, context_id=context_id)
            raise ValueError("step 또는 group 중 하나는 반드시 제공되어야 합니다")

        running_pods = [pod for pod in pods if pod.status.phase == "Running"]
        if len(running_pods) != len(pods):
            debug_print("⚠️ 일부 Pod가 Running 상태가 아님", 
                    context_type=context_type, 
                    context_id=context_id,
                    total_pods=len(pods),
                    running_pods=len(running_pods))

        debug_print("🔧 Pod 병렬 태스크 생성 시작", context_type=context_type, context_id=context_id, running_pod_count=len(running_pods))
        tasks = []
        for i, pod in enumerate(running_pods):
            debug_print(f"🔧 Pod 태스크 생성", 
                    context_type=context_type, 
                    context_id=context_id,
                    pod_name=pod.metadata.name,
                    pod_index=i)
            task = self.execute_rosbag_on_pod_direct(pod, simulation, step=step, group=group)
            tasks.append(task)

        try:
            debug_print("⏳ 모든 Pod 태스크 완료 대기", context_type=context_type, context_id=context_id, task_count=len(tasks))
            results = await asyncio.gather(*tasks, return_exceptions=False)
            
            success_count = sum(1 for r in results if r.status == ExecutionStatus.SUCCESS)
            failed_count = sum(1 for r in results if r.status == ExecutionStatus.FAILED)
            timeout_count = sum(1 for r in results if r.status == ExecutionStatus.TIMEOUT)
            
            debug_print("✅ 모든 Pod 태스크 완료", 
                    context_type=context_type, 
                    context_id=context_id,
                    success_count=success_count,
                    failed_count=failed_count,
                    timeout_count=timeout_count)

            failed_results = [r for r in results if r.status != ExecutionStatus.SUCCESS]
            for result in failed_results:
                debug_print("❌ Pod 실행 실패", 
                        context_type=context_type,
                        context_id=context_id,
                        pod_name=result.pod_name,
                        error=result.message)

            return results
            
        except Exception as e:
            debug_print("💥 Pod 병렬 실행 중 치명적 오류", 
                    context_type=context_type, 
                    context_id=context_id,
                    error=str(e),
                    error_type=type(e).__name__)
            raise
    
    def get_execution_summary(self, results: List[PodExecutionResult]) -> Dict[str, Any]:
        total = len(results)
        success = sum(1 for r in results if r.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for r in results if r.status == ExecutionStatus.FAILED)
        timeout = sum(1 for r in results if r.status == ExecutionStatus.TIMEOUT)
        
        avg_execution_time = sum(r.execution_time for r in results) / total if total > 0 else 0
        
        return {
            "total_pods": total,
            "success_count": success,
            "failed_count": failed,
            "timeout_count": timeout,
            "success_rate": (success / total * 100) if total > 0 else 0,
            "average_execution_time": round(avg_execution_time, 2),
            "failed_pods": [r.pod_name for r in results if r.status != ExecutionStatus.SUCCESS]
        }