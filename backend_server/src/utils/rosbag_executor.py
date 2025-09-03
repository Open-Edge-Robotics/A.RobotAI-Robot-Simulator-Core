from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from kubernetes.client import V1Pod
from urllib.parse import quote

from crud.pod import PodService
from crud.rosbag import RosService
from utils.debug_print import debug_print
from models.simulation import Simulation
from models.simulation_steps import SimulationStep
from models.simulation_groups import SimulationGroup
from models.enums import ExecutionStatus

logger = logging.getLogger(__name__)

@dataclass
class PodExecutionResult:
    pod_name: str
    pod_ip: str
    status: ExecutionStatus
    message: str
    response_data: Dict = None
    execution_time: float = 0.0
    start_time: datetime = None
    end_time: datetime = None

@dataclass
class RosbagStopResult:
    """rosbag 중지 결과를 담는 데이터 클래스"""
    pod_name: str
    status: str  # "stopped", "already_stopped", "failed", "timeout"
    message: str = ""
    stop_time: str = None
    error: str = None    


class RosbagExecutor:
    DEFAULT_TIMEOUT = 300
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    def __init__(self, pod_service: PodService, ros_service: RosService):
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
            rosbag_params = {
                "object_path": bag_file_path
            }
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

    async def _start_rosbag_on_single_pod(self, pod: V1Pod, execution_context: str) -> PodExecutionResult:
        """단일 Pod rosbag 시작 (완료 대기하지 않음)"""
        start_time = datetime.now(timezone.utc)
        pod_name = pod.metadata.name

        try:
            debug_print(f"[{execution_context}] Pod {pod_name}에서 rosbag 시작 요청")

            bag_file_path = self._extract_bag_file_path_from_pod(pod)
            if not bag_file_path:
                raise ValueError(f"Pod {pod_name}에 BAG_FILE_PATH 환경변수가 없음")

            if pod.status.phase != "Running":
                raise ValueError(f"Pod {pod_name} 상태가 Running이 아님: {pod.status.phase}")

            pod_ip = pod.status.pod_ip
            if not pod_ip:
                raise ValueError(f"Pod {pod_name}에 IP가 할당되지 않음")

            # rosbag 파라미터 준비
            rosbag_params = {
                "object_path": bag_file_path
            }
            debug_print(f"[{execution_context}] Pod {pod_name} rosbag 파라미터: {rosbag_params}")

            # rosbag 시작 요청 (완료까지 대기하지 않음)
            response = await self._execute_with_retry(pod_ip, rosbag_params, pod_name)

            execution_time = datetime.now(timezone.utc) - start_time
            execution_seconds = execution_time.total_seconds()

            debug_print(f"[{execution_context}] Pod {pod_name} rosbag 시작 성공 (소요시간: {execution_seconds}초)")

            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod_ip,
                status=ExecutionStatus.SUCCESS,
                message="Rosbag 시작 성공",
                response_data=response,
                execution_time=execution_seconds
            )

        except asyncio.TimeoutError:
            execution_time = datetime.now(timezone.utc) - start_time
            execution_seconds = execution_time.total_seconds()
            error_msg = f"Pod {pod_name} rosbag 시작 타임아웃 ({self.DEFAULT_TIMEOUT}초)"
            debug_print(f"[{execution_context}] {error_msg}")
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
            error_msg = f"Pod {pod_name} rosbag 시작 실패: {str(e)}"
            logger.error(f"[{execution_context}] {error_msg}")
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod.status.pod_ip or "unknown",
                status=ExecutionStatus.FAILED,
                message=error_msg,
                execution_time=execution_seconds
            )
    
    async def execute_single_pod(
        self,
        pod: V1Pod,
        simulation,
        group: Optional[SimulationGroup] = None,
        step: Optional[SimulationStep] = None
    ) -> PodExecutionResult:
        """
        단일 Pod에서 rosbag 실행
        - 비동기 호출
        - Pod 실행 결과를 PodExecutionResult로 반환
        - Cancel 시 stop_rosbag 즉시 실행
        """
        pod_name = pod.metadata.name
        start_time = datetime.now(timezone.utc)

        # prefix 설정
        prefix = ""
        if group:
            prefix += f"[Group {group.id}] "
        if step:
            prefix += f"[Step {step.step_order}] "
        prefix += f"[Pod {pod_name}]"

        debug_print(f"{prefix} ▶ Pod 실행 시작")

        try:
            # 1️⃣ Pod 실행 요청
            await self._start_rosbag_on_single_pod(pod, execution_context=prefix)

            # 2️⃣ Pod 완료까지 폴링
            poll_interval = 1
            max_wait = 3600
            elapsed = 0
            while elapsed < max_wait:
                pod_status = await self._check_pod_rosbag_status(pod)
                is_playing = pod_status.get("is_playing", False)

                if not is_playing:
                    debug_print(f"{prefix} ✅ Pod 실행 완료")
                    return PodExecutionResult(
                        pod_name=pod_name,
                        pod_ip=pod.status.pod_ip or "unknown",
                        status=ExecutionStatus.SUCCESS,
                        message="완료",
                        start_time=start_time,
                        end_time=datetime.now(timezone.utc)
                    )

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            debug_print(f"{prefix} ⏰ Pod 실행 타임아웃")
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod.status.pod_ip or "unknown",
                status=ExecutionStatus.TIMEOUT,
                message=f"타임아웃({max_wait}s)",
                start_time=start_time,
                end_time=datetime.now(timezone.utc)
            )

        except asyncio.CancelledError:
            # Cancel 감지 시 즉시 stop 호출
            debug_print(f"{prefix} 🛑 CancelledError 감지, 즉시 중지 시작")
            stop_result = await self._stop_single_pod_rosbag_with_result(pod, execution_context=prefix)
            debug_print(f"{prefix} 🛑 Cancel 처리 완료, 상태={stop_result.status}")
            return stop_result

        except Exception as e:
            debug_print(f"{prefix} 💥 Pod 실행 예외 발생: {e}")
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod.status.pod_ip or "unknown",
                status=ExecutionStatus.FAILED,
                message=str(e),
                start_time=start_time,
                end_time=datetime.now(timezone.utc)
            )

    async def _check_pod_rosbag_status(self, pod: V1Pod) -> dict:
        """단일 Pod rosbag 상태 체크"""
        pod_name = pod.metadata.name
        
        try:
            pod_ip = self.pod_service.get_v1pod_ip(pod)
            
            status_response = await asyncio.wait_for(
                self.ros_service.get_pod_status(pod_ip),
                timeout=10.0
            )
            
            return status_response
            
        except Exception as e:
            raise Exception(f"Pod {pod_name} 상태 체크 실패: {str(e)}")

    async def _collect_final_results(self, pods: List[V1Pod], execution_context,
                                        stop_event: Optional[asyncio.Event] = None) -> List[PodExecutionResult]:
        """최종 실행 결과 수집"""
        results = []
        is_stopped = stop_event and stop_event.is_set()
        
        debug_print(f"{execution_context} 📊 최종 결과 수집 시작 - stop_event.is_set()={is_stopped}")
        
        for pod in pods:
            pod_name = pod.metadata.name
            debug_print(f"{execution_context} 🔍 Pod [{pod_name}] 최종 상태 수집 중...")
            
            try:
                final_status = await self._check_pod_rosbag_status(pod)
                debug_print(f"{execution_context} 📋 Pod [{pod_name}] 최종 상태: {final_status}")
                
                is_playing = final_status.get("isPlaying", False)
                stop_reason = final_status.get("stopReason")
                current_loop = final_status.get("current_loop", 0)
                max_loops = final_status.get("max_loops", 0)
                
                debug_print(f"{execution_context} 📈 Pod [{pod_name}] 상태 분석:")
                debug_print(f"  - isPlaying: {is_playing}")
                debug_print(f"  - stopReason: {stop_reason}")
                debug_print(f"  - current_loop: {current_loop}")
                debug_print(f"  - max_loops: {max_loops}")
                
                # 상태 및 메시지 결정
                if is_stopped:
                    # 사용자가 중지 이벤트를 발생시킨 경우
                    status = ExecutionStatus.SUCCESS  # 중지도 성공으로 간주
                    message = f"사용자 요청으로 중지됨 ({current_loop}/{max_loops} loops 완료)"
                    debug_print(f"{execution_context} ⏹️ Pod [{pod_name}] 사용자 중지")
                    
                elif stop_reason == "completed":
                    # 정상 완료
                    status = ExecutionStatus.SUCCESS
                    message = f"Rosbag 재생 완료 ({current_loop}/{max_loops} loops)"
                    debug_print(f"{execution_context} ✅ Pod [{pod_name}] 정상 완료")
                    
                elif stop_reason == "user_stopped":
                    # Pod 자체에서 사용자 중지
                    status = ExecutionStatus.SUCCESS
                    message = f"사용자가 중지 ({current_loop}/{max_loops} loops 완료)"
                    debug_print(f"{execution_context} ⏹️ Pod [{pod_name}] Pod 내부 사용자 중지")
                    
                elif stop_reason == "failed":
                    # 실행 중 실패
                    status = ExecutionStatus.FAILED
                    error_detail = final_status.get("error", "unknown error")
                    message = f"Rosbag 재생 실패: {error_detail} ({current_loop}/{max_loops} loops)"
                    debug_print(f"{execution_context} ❌ Pod [{pod_name}] 실행 실패")
                    
                elif is_playing:
                    # 아직 실행 중 (비정상적인 상황)
                    status = ExecutionStatus.FAILED
                    message = f"비정상 상태: 아직 실행 중 ({current_loop}/{max_loops} loops)"
                    debug_print(f"{execution_context} ⚠️ Pod [{pod_name}] 비정상 상태 - 아직 실행 중")
                    
                else:
                    # 알 수 없는 상태
                    status = ExecutionStatus.FAILED
                    message = f"알 수 없는 상태 (stopReason: {stop_reason}, {current_loop}/{max_loops} loops)"
                    debug_print(f"{execution_context} ❓ Pod [{pod_name}] 알 수 없는 상태")
                
                results.append(PodExecutionResult(
                    pod_name=pod_name,
                    pod_ip=pod.status.pod_ip or "unknown",
                    status=status,
                    message=message,
                    response_data=final_status,
                    execution_time=0.0  # 전체 실행 시간은 별도 계산 필요
                ))
                
                debug_print(f"{execution_context} 📝 Pod [{pod_name}] 결과 생성 완료 - status: {status.value}")
                
            except Exception as e:
                error_msg = f"최종 상태 수집 실패: {str(e)}"
                debug_print(f"{execution_context} ❌ Pod [{pod_name}] 예외 발생: {error_msg}")
                
                results.append(PodExecutionResult(
                    pod_name=pod_name,
                    pod_ip=pod.status.pod_ip or "unknown",
                    status=ExecutionStatus.FAILED,
                    message=error_msg,
                    execution_time=0.0
                ))
        
        success_count = sum(1 for r in results if r.status == ExecutionStatus.SUCCESS)
        failed_count = len(results) - success_count
        
        debug_print(f"{execution_context} 📊 최종 결과 수집 완료:")
        debug_print(f"  - 성공: {success_count}개")
        debug_print(f"  - 실패: {failed_count}개") 
        debug_print(f"  - 중지 이벤트: {is_stopped}")
        
        return results

    # =====================================
    # Pod 중지 실행 메서드들
    # =====================================
    
    async def stop_rosbag_parallel_pods(self, pods: List[V1Pod], step_order: int) -> List[PodExecutionResult]:
        """
        특정 스텝의 모든 Pod rosbag 병렬 중지
        (순차 시뮬레이션에서 호출)
        """
        execution_context = f"[Step {step_order}]"
        debug_print(f"{execution_context} 🛑 rosbag 병렬 중지 시작", pod_count=len(pods))
        
        if not pods:
            debug_print(f"{execution_context} ⚠️ 중지할 Pod 없음")
            return []
        
        # 병렬로 모든 Pod 중지
        stop_tasks = []
        for pod in pods:
            task = self._stop_single_pod_rosbag_with_result(pod, execution_context)
            stop_tasks.append(task)
        
        debug_print(f"{execution_context} 🚀 {len(pods)}개 Pod 중지 요청 전송")
        
        # 모든 중지 작업 실행
        start_time = datetime.now(timezone.utc)
        results = await asyncio.gather(*stop_tasks, return_exceptions=False)
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        # 결과 분석
        stopped_count = sum(1 for r in results if r.status == "stopped")
        failed_count = sum(1 for r in results if r.status in ["failed", "timeout"])
        already_stopped_count = sum(1 for r in results if r.status == "already_stopped")
        
        debug_print(f"{execution_context} ✅ rosbag 병렬 중지 완료", 
                   stopped_count=stopped_count,
                   failed_count=failed_count,
                   already_stopped_count=already_stopped_count,
                   total_time=f"{elapsed:.1f}초")
        
        return results

    async def stop_rosbag_parallel_all_pods(self, pods: List[V1Pod]) -> List[PodExecutionResult]:
        """
        모든 Pod rosbag 동시 중지 (병렬 시뮬레이션에서 호출)
        """
        execution_context = "[Parallel]"
        debug_print(f"{execution_context} ⚡ 모든 Pod 동시 중지 시작", pod_count=len(pods))

        if not pods:
            debug_print(f"{execution_context} ⚠️ 중지할 Pod 없음")
            return []

        # 1️⃣ 각 Pod 코루틴을 Task로 변환
        stop_tasks = [
            asyncio.create_task(self._stop_single_pod_rosbag_with_result(pod, execution_context))
            for pod in pods
        ]
        debug_print(f"{execution_context} 🚀 {len(stop_tasks)}개 Pod 중지 Task 생성 완료")

        # 2️⃣ 모든 태스크 동시에 실행
        start_time = datetime.now(timezone.utc)
        results: List[PodExecutionResult] = []
        try:
            results = await asyncio.gather(*stop_tasks, return_exceptions=False)
        except Exception as e:
            debug_print(f"{execution_context} ❌ 모든 Pod 중지 중 예외 발생: {e}")
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        # 3️⃣ 결과 분석 및 로그
        stopped_count = sum(1 for r in results if r.status == "stopped")
        failed_count = sum(1 for r in results if r.status in ["failed", "timeout"])
        already_stopped_count = sum(1 for r in results if r.status == "already_stopped")

        debug_print(
            f"{execution_context} ⚡ 모든 Pod 동시 중지 완료",
            stopped_count=stopped_count,
            failed_count=failed_count,
            already_stopped_count=already_stopped_count,
            total_time=f"{elapsed:.1f}초"
        )

        return results


    # =====================================
    # 단일 Pod 중지 처리
    # =====================================

    async def _stop_single_pod_rosbag_with_result(
        self, pod: V1Pod, execution_context: str
    ) -> PodExecutionResult:
        """단일 Pod rosbag 중지 후 PodExecutionResult 반환"""
        pod_name = pod.metadata.name
        start_time = datetime.now(timezone.utc)
        pod_ip = self.pod_service.get_v1pod_ip(pod)
        
        try:
            debug_print(f"{execution_context} 🛑 Pod {pod_name} rosbag 중지 시작")
            if not pod_ip:
                raise ValueError(f"Pod {pod_name}의 IP를 가져올 수 없음")
            
            # 현재 상태 확인
            try:
                current_status = await self._check_pod_rosbag_status(pod)
                is_dict = isinstance(current_status, dict)
                is_playing = current_status.get("is_playing", False) if is_dict else False
                debug_print(f"{execution_context} ▶️ Pod {pod_name} is_playing={is_playing}")

                if not is_playing:
                    execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
                    debug_print(f"{execution_context} ℹ️ Pod {pod_name} 이미 중지 상태, 실행 시간={execution_time:.2f}초")
                    return PodExecutionResult(
                        pod_name=pod_name,
                        pod_ip=pod_ip,
                        status=ExecutionStatus.ALREADY_STOPPED,
                        message="rosbag이 이미 중지된 상태입니다",
                        execution_time=execution_time,
                        start_time=start_time,
                        end_time=datetime.now(timezone.utc)
                    )
            except Exception as status_error:
                debug_print(f"{execution_context} ⚠️ Pod {pod_name} 상태 확인 중 예외 발생: {status_error}")
                import traceback
                debug_print(traceback.format_exc())

            # 실제 rosbag 중지 요청
            stop_response = await asyncio.wait_for(self.ros_service.stop_rosbag(pod_ip), timeout=10.0)
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            debug_print(f"{execution_context} ✅ Pod {pod_name} rosbag 중지 성공")

            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod_ip,
                status=ExecutionStatus.STOPPED,
                message="정상적으로 중지됨",
                response_data=stop_response,
                execution_time=execution_time,
                start_time=start_time,
                end_time=datetime.now(timezone.utc)
            )

        except asyncio.TimeoutError:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            debug_print(f"{execution_context} ⏰ Pod {pod_name} 중지 타임아웃")
            try:
                await self._force_terminate_pod_process(pod, execution_context)
                return PodExecutionResult(
                    pod_name=pod_name,
                    pod_ip=pod_ip,
                    status=ExecutionStatus.TIMEOUT,
                    message="타임아웃 후 강제 종료됨",
                    execution_time=execution_time,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc)
                )
            except Exception as force_error:
                return PodExecutionResult(
                    pod_name=pod_name,
                    pod_ip=pod_ip,
                    status=ExecutionStatus.FAILED,
                    message="중지 타임아웃 및 강제 종료 실패",
                    response_data={"error": str(force_error)},
                    execution_time=execution_time,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc)
                )

        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            debug_print(f"{execution_context} ❌ Pod {pod_name} rosbag 중지 실패: {e}")
            return PodExecutionResult(
                pod_name=pod_name,
                pod_ip=pod_ip,
                status=ExecutionStatus.FAILED,
                message=f"Pod {pod_name} rosbag 중지 실패: {str(e)}",
                response_data={"error": str(e)},
                execution_time=execution_time,
                start_time=start_time,
                end_time=datetime.now(timezone.utc)
            )