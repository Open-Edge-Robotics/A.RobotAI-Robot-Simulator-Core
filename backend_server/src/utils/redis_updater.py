"""
Redis 업데이트 최적화 모듈
- Pattern별 최적화된 업데이트 전략
- 성능 측정 데코레이터 포함
"""
import sys
import os

# tests/에서 한 단계 위인 backend_server를 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Literal
from functools import wraps
from collections import defaultdict

from database.redis_conn import RedisConnection
from utils.debug_print import debug_print


class RedisUpdateMetrics:
    """Redis 업데이트 성능 측정"""

    def __init__(self):
        self.metrics = defaultdict(lambda: {
            "count": 0,
            "total_time": 0.0,
            "total_bytes_read": 0,
            "total_bytes_written": 0,
            "min_time": float('inf'),
            "max_time": 0.0
        })

    def record(
        self,
        operation: str,
        duration: float,
        bytes_read: int = 0,
        bytes_written: int = 0
    ):
        """메트릭 기록"""
        m = self.metrics[operation]
        m["count"] += 1
        m["total_time"] += duration
        m["total_bytes_read"] += bytes_read
        m["total_bytes_written"] += bytes_written
        m["min_time"] = min(m["min_time"], duration)
        m["max_time"] = max(m["max_time"], duration)

    def get_summary(self) -> Dict[str, Any]:
        """성능 요약 반환"""
        summary = {}
        for operation, data in self.metrics.items():
            if data["count"] == 0:
                continue

            avg_time = data["total_time"] / data["count"]
            avg_bytes_read = data["total_bytes_read"] / data["count"]
            avg_bytes_written = data["total_bytes_written"] / data["count"]

            summary[operation] = {
                "호출_횟수": data["count"],
                "총_소요시간_ms": round(data["total_time"] * 1000, 2),
                "평균_소요시간_ms": round(avg_time * 1000, 2),
                "최소_소요시간_ms": round(data["min_time"] * 1000, 2),
                "최대_소요시간_ms": round(data["max_time"] * 1000, 2),
                "총_읽기_바이트": data["total_bytes_read"],
                "평균_읽기_바이트": round(avg_bytes_read, 2),
                "총_쓰기_바이트": data["total_bytes_written"],
                "평균_쓰기_바이트": round(avg_bytes_written, 2),
            }
        return summary

    def reset(self):
        """메트릭 초기화"""
        self.metrics.clear()


# 전역 메트릭 인스턴스
_global_metrics = RedisUpdateMetrics()


def measure_redis_update(operation_name: str):
    """Redis 업데이트 성능 측정 데코레이터"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()

            # 함수 실행
            result = await func(*args, **kwargs)

            # 소요 시간 기록
            duration = time.perf_counter() - start_time

            # 메트릭 기록 (바이트 크기는 result에서 추출 가능하면 추출)
            bytes_info = {}
            if isinstance(result, dict):
                bytes_info = {
                    "bytes_read": result.get("_bytes_read", 0),
                    "bytes_written": result.get("_bytes_written", 0)
                }

            _global_metrics.record(
                operation_name,
                duration,
                bytes_info.get("bytes_read", 0),
                bytes_info.get("bytes_written", 0)
            )

            return result
        return wrapper
    return decorator


def get_metrics_summary() -> Dict[str, Any]:
    """전역 메트릭 요약 조회"""
    return _global_metrics.get_summary()


def reset_metrics():
    """전역 메트릭 초기화"""
    _global_metrics.reset()


class OptimizedRedisUpdater:
    """
    패턴별 최적화된 Redis 업데이트

    개선점:
    1. Sequential: step 단위로 해시 필드 업데이트 (HSET 사용)
    2. Parallel: group 단위로 해시 필드 업데이트 (HSET 사용)
    3. 전체 데이터 GET/SET 최소화
    """

    def __init__(self, redis_connection: RedisConnection):
        self.redis = redis_connection.client

    # ========================================
    # Sequential 패턴용 최적화
    # ========================================

    @measure_redis_update("sequential_step_update")
    async def update_sequential_step_status(
        self,
        simulation_id: int,
        execution_id: int,
        step_order: int,
        status: str,
        progress: Optional[float] = None,
        current_repeat: Optional[int] = None,
        total_repeats: Optional[int] = None,
        autonomous_agents: Optional[int] = None,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        순차 패턴: Step 상태만 업데이트 (부분 업데이트)

        개선 방식:
        - 전체 데이터 대신 stepDetails 배열만 조회
        - 해당 step만 수정 후 다시 저장
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        steps_hash = f"{execution_key}:steps"
        now = datetime.now(timezone.utc)

        step_field = f"step:{step_order}"

        # Try to get the individual step from a hash (partial-update storage)
        try:
            step_raw = await self.redis.hget(steps_hash, step_field)
        except AttributeError:
            # If client doesn't support hget (unlikely), fallback to full GET
            step_raw = None

        # If step not present in hash, try to migrate from full execution JSON
        if not step_raw:
            full_raw = await self.redis.get(execution_key)
            if not full_raw:
                # Nothing to update
                return {"_bytes_read": 0, "_bytes_written": 0}

            full_raw_text = full_raw.decode('utf-8') if isinstance(full_raw, bytes) else full_raw
            try:
                full_obj = json.loads(full_raw_text)
            except json.JSONDecodeError:
                return {"_bytes_read": len(full_raw_text.encode('utf-8')), "_bytes_written": 0}

            # Migrate each step into hash fields
            step_details = full_obj.get("stepDetails", [])
            for s in step_details:
                field = f"step:{s.get('stepOrder')}"
                await self.redis.hset(steps_hash, field, json.dumps(s))

            # after migration, get the specific step
            step_raw = await self.redis.hget(steps_hash, step_field)

        if not step_raw:
            debug_print(f"⚠️ Step {step_order}를 찾을 수 없음")
            return {"_bytes_read": 0, "_bytes_written": 0}

        # Normalize raw bytes/str
        step_text = step_raw.decode('utf-8') if isinstance(step_raw, bytes) else step_raw
        bytes_read = len(step_text.encode('utf-8'))

        try:
            step_obj = json.loads(step_text)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # Capture previous status BEFORE mutating for accurate meta update
        prev_status = step_obj.get("status")

        # Update step object in-place
        step_obj["status"] = status
        if progress is not None:
            step_obj["progress"] = progress
        if current_repeat is not None:
            step_obj["currentRepeat"] = current_repeat
        if total_repeats is not None:
            step_obj["totalRepeats"] = total_repeats
        if autonomous_agents is not None:
            step_obj["autonomousAgents"] = autonomous_agents

        # timestamp updates
        if status == "RUNNING" and not step_obj.get("startedAt"):
            step_obj["startedAt"] = now.isoformat()
        elif status == "COMPLETED":
            step_obj["completedAt"] = now.isoformat()
        elif status == "FAILED":
            step_obj["failedAt"] = now.isoformat()
            step_obj["error"] = error
        elif status == "STOPPED":
            step_obj["stoppedAt"] = now.isoformat()
            step_obj["error"] = error

        # Write back only the updated step into the hash
        updated_step = json.dumps(step_obj)
        bytes_written = len(updated_step.encode('utf-8'))
        await self.redis.hset(steps_hash, step_field, updated_step)

        # Update meta incrementally using prev_status to determine transitions
        try:
            meta_raw = await self.redis.hget(steps_hash, "meta")
            if meta_raw:
                meta_text = meta_raw.decode('utf-8') if isinstance(meta_raw, bytes) else meta_raw
                try:
                    meta = json.loads(meta_text)
                except Exception:
                    meta = None
            else:
                meta = None

            if meta is None:
                # Initialize meta from migration data (hgetall fallback)
                try:
                    all_fields = await self.redis.hgetall(steps_hash)
                    total_steps = sum(1 for k in all_fields.keys() if k.startswith("step:"))
                    completed_steps = 0
                    running_step = 0
                    for k, v in all_fields.items():
                        if not k.startswith("step:"):
                            continue
                        val_text = v.decode('utf-8') if isinstance(v, bytes) else v
                        try:
                            so = json.loads(val_text)
                        except Exception:
                            continue
                        if so.get("status") == "COMPLETED":
                            completed_steps += 1
                        if so.get("status") == "RUNNING":
                            running_step = so.get("stepOrder", running_step)

                    meta = {"totalSteps": total_steps, "completedSteps": completed_steps, "currentStep": running_step}
                except AttributeError:
                    # cannot hgetall; set conservative defaults
                    meta = {"totalSteps": 0, "completedSteps": 0, "currentStep": 0}

            # Use prev_status to detect meaningful transitions
            # If step moved into COMPLETED from a non-COMPLETED state -> increment completedSteps
            if status == "COMPLETED" and prev_status != "COMPLETED":
                meta["completedSteps"] = meta.get("completedSteps", 0) + 1

            # If step moved into RUNNING from a non-RUNNING state -> set currentStep
            if status == "RUNNING" and prev_status != "RUNNING":
                meta["currentStep"] = step_order

            # If step was RUNNING and moved away (e.g., STOPPED/COMPLETED),
            # clear currentStep only if it referred to this step
            if prev_status == "RUNNING" and status != "RUNNING":
                if meta.get("currentStep") == step_order:
                    # Try to find another running step or reset to 0
                    # Simple approach: unset to 0 (consumer can recalc if needed)
                    meta["currentStep"] = 0

            await self.redis.hset(steps_hash, "meta", json.dumps(meta))
        except AttributeError:
            # client doesn't support hget/hset; ignore
            pass

        return {"_bytes_read": bytes_read, "_bytes_written": bytes_written}

    @measure_redis_update("sequential_simulation_update")
    async def update_sequential_simulation_status(
        self,
        simulation_id: int,
        execution_id: int,
        status: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        순차 패턴: 전체 시뮬레이션 상태 업데이트
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        now = datetime.now(timezone.utc)

        # 현재 상태 조회
        raw_data = await self.redis.get(execution_key)
        if not raw_data:
            return {"_bytes_read": 0, "_bytes_written": 0}

        bytes_read = len(raw_data) if isinstance(raw_data, bytes) else len(raw_data.encode('utf-8'))

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")

        try:
            current_status = json.loads(raw_data)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # 상태 업데이트
        current_status["status"] = status

        # 타임스탬프 업데이트
        current_status["timestamps"]["lastUpdated"] = now.isoformat()
        if status == "COMPLETED":
            current_status["timestamps"]["completedAt"] = now.isoformat()
        elif status == "FAILED":
            current_status["timestamps"]["failedAt"] = now.isoformat()
        elif status == "STOPPED":
            current_status["timestamps"]["stoppedAt"] = now.isoformat()

        # 메시지 업데이트
        if reason:
            current_status["message"] = reason

        # Redis에 저장
        updated_data = json.dumps(current_status)
        bytes_written = len(updated_data.encode('utf-8'))

        await self.redis.set(execution_key, updated_data)

        # Primary key도 동일하게 업데이트
        primary_key = f"simulation:{simulation_id}"
        await self.redis.set(primary_key, updated_data)

        return {
            "_bytes_read": bytes_read,
            "_bytes_written": bytes_written * 2
        }

    # ========================================
    # Parallel 패턴용 최적화
    # ========================================

    @measure_redis_update("parallel_group_update")
    async def update_parallel_group_status(
        self,
        simulation_id: int,
        execution_id: int,
        group_id: int,
        status: str,
        progress: Optional[float] = None,
        current_repeat: Optional[int] = None,
        total_repeats: Optional[int] = None,
        autonomous_agents: Optional[int] = None,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        병렬 패턴: Group 상태만 업데이트 (부분 업데이트)

        개선 방식:
        - 전체 데이터 대신 groupDetails 배열만 조회
        - 해당 group만 수정 후 다시 저장
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        groups_hash = f"{execution_key}:groups"
        now = datetime.now(timezone.utc)

        group_field = f"group:{group_id}"

        # Try to get the individual group from a hash (partial-update storage)
        try:
            group_raw = await self.redis.hget(groups_hash, group_field)
        except AttributeError:
            group_raw = None

        # If group not present in hash, try to migrate from full execution JSON
        if not group_raw:
            full_raw = await self.redis.get(execution_key)
            if not full_raw:
                return {"_bytes_read": 0, "_bytes_written": 0}

            full_raw_text = full_raw.decode('utf-8') if isinstance(full_raw, bytes) else full_raw
            try:
                full_obj = json.loads(full_raw_text)
            except json.JSONDecodeError:
                return {"_bytes_read": len(full_raw_text.encode('utf-8')), "_bytes_written": 0}

            # Migrate each group into hash fields
            group_details = full_obj.get("groupDetails", [])
            for g in group_details:
                field = f"group:{g.get('groupId')}"
                await self.redis.hset(groups_hash, field, json.dumps(g))

            # after migration, get the specific group
            group_raw = await self.redis.hget(groups_hash, group_field)

        if not group_raw:
            debug_print(f"⚠️ Group {group_id}를 찾을 수 없음")
            return {"_bytes_read": 0, "_bytes_written": 0}

        # Normalize raw bytes/str
        group_text = group_raw.decode('utf-8') if isinstance(group_raw, bytes) else group_raw
        bytes_read = len(group_text.encode('utf-8'))

        try:
            group_obj = json.loads(group_text)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # Update group object in-place
        group_obj["status"] = status
        if progress is not None:
            group_obj["progress"] = progress
        if current_repeat is not None:
            group_obj["currentRepeat"] = current_repeat
        if total_repeats is not None:
            group_obj["totalRepeats"] = total_repeats
        if autonomous_agents is not None:
            group_obj["autonomousAgents"] = autonomous_agents

        # timestamp updates
        if status == "RUNNING" and not group_obj.get("startedAt"):
            group_obj["startedAt"] = now.isoformat()
        elif status == "COMPLETED":
            group_obj["completedAt"] = now.isoformat()
        elif status == "FAILED":
            group_obj["failedAt"] = now.isoformat()
            group_obj["error"] = error
        elif status == "STOPPED":
            group_obj["stoppedAt"] = now.isoformat()
            group_obj["error"] = error

        # Write back only the updated group into the hash
        updated_group = json.dumps(group_obj)
        bytes_written = len(updated_group.encode('utf-8'))
        await self.redis.hset(groups_hash, group_field, updated_group)

        # Update meta incrementally
        try:
            meta_raw = await self.redis.hget(groups_hash, "meta")
            if meta_raw:
                meta_text = meta_raw.decode('utf-8') if isinstance(meta_raw, bytes) else meta_raw
                try:
                    meta = json.loads(meta_text)
                except Exception:
                    meta = None
            else:
                meta = None

            if meta is None:
                try:
                    all_fields = await self.redis.hgetall(groups_hash)
                    total_groups = sum(1 for k in all_fields.keys() if k.startswith("group:"))
                    completed_groups = 0
                    running_groups = 0
                    for k, v in all_fields.items():
                        if not k.startswith("group:"):
                            continue
                        val_text = v.decode('utf-8') if isinstance(v, bytes) else v
                        try:
                            go = json.loads(val_text)
                        except Exception:
                            continue
                        if go.get("status") == "COMPLETED":
                            completed_groups += 1
                        if go.get("status") == "RUNNING":
                            running_groups += 1

                    meta = {"totalGroups": total_groups, "completedGroups": completed_groups, "runningGroups": running_groups}
                except AttributeError:
                    meta = {"totalGroups": 0, "completedGroups": 0, "runningGroups": 0}

            # Adjust meta based on this update
            if status == "COMPLETED":
                meta["completedGroups"] = meta.get("completedGroups", 0) + 1
            if status == "RUNNING":
                meta["runningGroups"] = meta.get("runningGroups", 0) + 1

            await self.redis.hset(groups_hash, "meta", json.dumps(meta))
        except AttributeError:
            pass

        return {"_bytes_read": bytes_read, "_bytes_written": bytes_written}

    @measure_redis_update("parallel_simulation_update")
    async def update_parallel_simulation_status(
        self,
        simulation_id: int,
        execution_id: int,
        status: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        병렬 패턴: 전체 시뮬레이션 상태 업데이트
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        now = datetime.now(timezone.utc)

        # 현재 상태 조회
        raw_data = await self.redis.get(execution_key)
        if not raw_data:
            return {"_bytes_read": 0, "_bytes_written": 0}

        bytes_read = len(raw_data) if isinstance(raw_data, bytes) else len(raw_data.encode('utf-8'))

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")

        try:
            current_status = json.loads(raw_data)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # 상태 업데이트
        current_status["status"] = status

        # 타임스탬프 업데이트
        current_status["timestamps"]["lastUpdated"] = now.isoformat()
        if status == "COMPLETED":
            current_status["timestamps"]["completedAt"] = now.isoformat()
        elif status == "FAILED":
            current_status["timestamps"]["failedAt"] = now.isoformat()
        elif status == "STOPPED":
            current_status["timestamps"]["stoppedAt"] = now.isoformat()

        # 메시지 업데이트
        if reason:
            current_status["message"] = reason

        # Redis에 저장
        updated_data = json.dumps(current_status)
        bytes_written = len(updated_data.encode('utf-8'))

        await self.redis.set(execution_key, updated_data)

        # Primary key도 동일하게 업데이트
        primary_key = f"simulation:{simulation_id}"
        await self.redis.set(primary_key, updated_data)

        return {
            "_bytes_read": bytes_read,
            "_bytes_written": bytes_written * 2
        }


class PerformanceReporter:
    """성능 개선 리포트 생성"""

    @staticmethod
    def generate_report() -> str:
        """
        성능 측정 결과를 기반으로 개선 리포트 생성

        Returns:
            마크다운 형식의 성능 리포트
        """
        summary = get_metrics_summary()

        if not summary:
            return "# ⚠️ 측정된 메트릭이 없습니다"

        report_lines = [
            "# 🚀 Redis 업데이트 성능 개선 리포트\n",
            f"**측정 시각**: {datetime.now(timezone.utc).isoformat()}\n",
            "---\n"
        ]

        # 작업별 성능 요약
        report_lines.append("## 📊 작업별 성능 측정 결과\n")

        for operation, metrics in summary.items():
            report_lines.append(f"### {operation}\n")
            report_lines.append(f"- **호출 횟수**: {metrics['호출_횟수']:,}회")
            report_lines.append(f"- **총 소요시간**: {metrics['총_소요시간_ms']:,.2f} ms")
            report_lines.append(f"- **평균 소요시간**: {metrics['평균_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **최소 소요시간**: {metrics['최소_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **최대 소요시간**: {metrics['최대_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **총 읽기 바이트**: {metrics['총_읽기_바이트']:,} bytes ({metrics['총_읽기_바이트'] / 1024:.2f} KB)")
            report_lines.append(f"- **평균 읽기 바이트**: {metrics['평균_읽기_바이트']:.2f} bytes")
            report_lines.append(f"- **총 쓰기 바이트**: {metrics['총_쓰기_바이트']:,} bytes ({metrics['총_쓰기_바이트'] / 1024:.2f} KB)")
            report_lines.append(f"- **평균 쓰기 바이트**: {metrics['평균_쓰기_바이트']:.2f} bytes\n")

        # 개선 효과 계산
        report_lines.append("## 💡 개선 효과 분석\n")

        # Sequential vs Parallel 비교
        seq_update = summary.get("sequential_step_update", {})
        par_update = summary.get("parallel_group_update", {})

        if seq_update and par_update:
            report_lines.append("### Sequential vs Parallel 업데이트 비교\n")
            report_lines.append(f"- Sequential 평균: {seq_update.get('평균_소요시간_ms', 0):.2f} ms")
            report_lines.append(f"- Parallel 평균: {par_update.get('평균_소요시간_ms', 0):.2f} ms")

            if seq_update.get('평균_소요시간_ms', 0) > par_update.get('평균_소요시간_ms', 0):
                improvement = ((seq_update['평균_소요시간_ms'] - par_update['평균_소요시간_ms'])
                               / seq_update['평균_소요시간_ms'] * 100)
                report_lines.append(f"- **Parallel이 {improvement:.1f}% 더 빠름**\n")
            else:
                improvement = ((par_update['평균_소요시간_ms'] - seq_update['평균_소요시간_ms'])
                               / par_update['평균_소요시간_ms'] * 100)
                report_lines.append(f"- **Sequential이 {improvement:.1f}% 더 빠름**\n")

        # 전체 통계
        total_operations = sum(m["호출_횟수"] for m in summary.values())
        total_time = sum(m["총_소요시간_ms"] for m in summary.values())
        total_read_kb = sum(m["총_읽기_바이트"] for m in summary.values()) / 1024
        total_write_kb = sum(m["총_쓰기_바이트"] for m in summary.values()) / 1024

        report_lines.append("### 전체 통계\n")
        report_lines.append(f"- **총 Redis 작업**: {total_operations:,}회")
        report_lines.append(f"- **총 소요시간**: {total_time:,.2f} ms ({total_time / 1000:.2f}초)")
        report_lines.append(f"- **총 데이터 읽기**: {total_read_kb:,.2f} KB")
        report_lines.append(f"- **총 데이터 쓰기**: {total_write_kb:,.2f} KB")
        report_lines.append(f"- **평균 작업 속도**: {total_time / max(total_operations, 1):.2f} ms/작업\n")

        # 개선 권장사항
        report_lines.append("## 🎯 개선 권장사항\n")

        # 느린 작업 식별
        slow_operations = [
            (op, m) for op, m in summary.items()
            if m.get("평균_소요시간_ms", 0) > 10  # 10ms 이상
        ]

        if slow_operations:
            report_lines.append("### ⚠️ 개선이 필요한 작업 (평균 10ms 이상)\n")
            for op, metrics in slow_operations:
                report_lines.append(
                    f"- **{op}**: {metrics['평균_소요시간_ms']:.2f} ms "
                    f"(호출 {metrics['호출_횟수']}회)"
                )
            report_lines.append("")

        # 데이터 전송량이 많은 작업
        heavy_operations = [
            (op, m) for op, m in summary.items()
            if m.get("평균_쓰기_바이트", 0) > 5000  # 5KB 이상
        ]

        if heavy_operations:
            report_lines.append("### 📦 데이터 전송량이 많은 작업 (평균 5KB 이상)\n")
            for op, metrics in heavy_operations:
                report_lines.append(
                    f"- **{op}**: {metrics['평균_쓰기_바이트'] / 1024:.2f} KB "
                    f"(호출 {metrics['호출_횟수']}회)"
                )
            report_lines.append("")

        report_lines.append("---\n")
        report_lines.append("*이 리포트는 자동 생성되었습니다*")

        return "\n".join(report_lines)

    @measure_redis_update("sequential_simulation_update")
    async def update_sequential_simulation_status(
        self,
        simulation_id: int,
        execution_id: int,
        status: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        순차 패턴: 전체 시뮬레이션 상태 업데이트
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        now = datetime.now(timezone.utc)

        # 현재 상태 조회
        raw_data = await self.redis.get(execution_key)
        if not raw_data:
            return {"_bytes_read": 0, "_bytes_written": 0}

        bytes_read = len(raw_data) if isinstance(raw_data, bytes) else len(raw_data.encode('utf-8'))

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")

        try:
            current_status = json.loads(raw_data)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # 상태 업데이트
        current_status["status"] = status

        # 타임스탬프 업데이트
        current_status["timestamps"]["lastUpdated"] = now.isoformat()
        if status == "COMPLETED":
            current_status["timestamps"]["completedAt"] = now.isoformat()
        elif status == "FAILED":
            current_status["timestamps"]["failedAt"] = now.isoformat()
        elif status == "STOPPED":
            current_status["timestamps"]["stoppedAt"] = now.isoformat()

        # 메시지 업데이트
        if reason:
            current_status["message"] = reason

        # Redis에 저장
        updated_data = json.dumps(current_status)
        bytes_written = len(updated_data.encode('utf-8'))

        await self.redis.set(execution_key, updated_data)

        # Primary key도 동일하게 업데이트
        primary_key = f"simulation:{simulation_id}"
        await self.redis.set(primary_key, updated_data)

        return {
            "_bytes_read": bytes_read,
            "_bytes_written": bytes_written * 2
        }

    # ========================================
    # Parallel 패턴용 최적화
    # ========================================

    @measure_redis_update("parallel_group_update")
    async def update_parallel_group_status(
        self,
        simulation_id: int,
        execution_id: int,
        group_id: int,
        status: str,
        progress: Optional[float] = None,
        current_repeat: Optional[int] = None,
        total_repeats: Optional[int] = None,
        autonomous_agents: Optional[int] = None,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        병렬 패턴: Group 상태만 업데이트 (부분 업데이트)

        개선 방식:
        - 전체 데이터 대신 groupDetails 배열만 조회
        - 해당 group만 수정 후 다시 저장
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        groups_hash = f"{execution_key}:groups"
        now = datetime.now(timezone.utc)

        group_field = f"group:{group_id}"

        # Try to get the individual group from a hash (partial-update storage)
        try:
            group_raw = await self.redis.hget(groups_hash, group_field)
        except AttributeError:
            group_raw = None

        # If group not present in hash, try to migrate from full execution JSON
        if not group_raw:
            full_raw = await self.redis.get(execution_key)
            if not full_raw:
                return {"_bytes_read": 0, "_bytes_written": 0}

            full_raw_text = full_raw.decode('utf-8') if isinstance(full_raw, bytes) else full_raw
            try:
                full_obj = json.loads(full_raw_text)
            except json.JSONDecodeError:
                return {"_bytes_read": len(full_raw_text.encode('utf-8')), "_bytes_written": 0}

            # Migrate each group into hash fields
            group_details = full_obj.get("groupDetails", [])
            for g in group_details:
                field = f"group:{g.get('groupId')}"
                await self.redis.hset(groups_hash, field, json.dumps(g))

            # after migration, get the specific group
            group_raw = await self.redis.hget(groups_hash, group_field)

        if not group_raw:
            debug_print(f"⚠️ Group {group_id}를 찾을 수 없음")
            return {"_bytes_read": 0, "_bytes_written": 0}

        # Normalize raw bytes/str
        group_text = group_raw.decode('utf-8') if isinstance(group_raw, bytes) else group_raw
        bytes_read = len(group_text.encode('utf-8'))

        try:
            group_obj = json.loads(group_text)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # Update group object in-place
        group_obj["status"] = status
        if progress is not None:
            group_obj["progress"] = progress
        if current_repeat is not None:
            group_obj["currentRepeat"] = current_repeat
        if total_repeats is not None:
            group_obj["totalRepeats"] = total_repeats
        if autonomous_agents is not None:
            group_obj["autonomousAgents"] = autonomous_agents

        # timestamp updates
        if status == "RUNNING" and not group_obj.get("startedAt"):
            group_obj["startedAt"] = now.isoformat()
        elif status == "COMPLETED":
            group_obj["completedAt"] = now.isoformat()
        elif status == "FAILED":
            group_obj["failedAt"] = now.isoformat()
            group_obj["error"] = error
        elif status == "STOPPED":
            group_obj["stoppedAt"] = now.isoformat()
            group_obj["error"] = error

        # Write back only the updated group into the hash
        updated_group = json.dumps(group_obj)
        bytes_written = len(updated_group.encode('utf-8'))
        await self.redis.hset(groups_hash, group_field, updated_group)

        # Update meta incrementally
        try:
            meta_raw = await self.redis.hget(groups_hash, "meta")
            if meta_raw:
                meta_text = meta_raw.decode('utf-8') if isinstance(meta_raw, bytes) else meta_raw
                try:
                    meta = json.loads(meta_text)
                except Exception:
                    meta = None
            else:
                meta = None

            if meta is None:
                try:
                    all_fields = await self.redis.hgetall(groups_hash)
                    total_groups = sum(1 for k in all_fields.keys() if k.startswith("group:"))
                    completed_groups = 0
                    running_groups = 0
                    for k, v in all_fields.items():
                        if not k.startswith("group:"):
                            continue
                        val_text = v.decode('utf-8') if isinstance(v, bytes) else v
                        try:
                            go = json.loads(val_text)
                        except Exception:
                            continue
                        if go.get("status") == "COMPLETED":
                            completed_groups += 1
                        if go.get("status") == "RUNNING":
                            running_groups += 1

                    meta = {"totalGroups": total_groups, "completedGroups": completed_groups, "runningGroups": running_groups}
                except AttributeError:
                    meta = {"totalGroups": 0, "completedGroups": 0, "runningGroups": 0}

            # Adjust meta based on this update
            if status == "COMPLETED":
                meta["completedGroups"] = meta.get("completedGroups", 0) + 1
            if status == "RUNNING":
                meta["runningGroups"] = meta.get("runningGroups", 0) + 1

            await self.redis.hset(groups_hash, "meta", json.dumps(meta))
        except AttributeError:
            pass

        return {"_bytes_read": bytes_read, "_bytes_written": bytes_written}

    @measure_redis_update("parallel_simulation_update")
    async def update_parallel_simulation_status(
        self,
        simulation_id: int,
        execution_id: int,
        status: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        병렬 패턴: 전체 시뮬레이션 상태 업데이트
        """
        execution_key = f"simulation:{simulation_id}:execution:{execution_id}"
        now = datetime.now(timezone.utc)

        # 현재 상태 조회
        raw_data = await self.redis.get(execution_key)
        if not raw_data:
            return {"_bytes_read": 0, "_bytes_written": 0}

        bytes_read = len(raw_data) if isinstance(raw_data, bytes) else len(raw_data.encode('utf-8'))

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")

        try:
            current_status = json.loads(raw_data)
        except json.JSONDecodeError:
            return {"_bytes_read": bytes_read, "_bytes_written": 0}

        # 상태 업데이트
        current_status["status"] = status

        # 타임스탬프 업데이트
        current_status["timestamps"]["lastUpdated"] = now.isoformat()
        if status == "COMPLETED":
            current_status["timestamps"]["completedAt"] = now.isoformat()
        elif status == "FAILED":
            current_status["timestamps"]["failedAt"] = now.isoformat()
        elif status == "STOPPED":
            current_status["timestamps"]["stoppedAt"] = now.isoformat()

        # 메시지 업데이트
        if reason:
            current_status["message"] = reason

        # Redis에 저장
        updated_data = json.dumps(current_status)
        bytes_written = len(updated_data.encode('utf-8'))

        await self.redis.set(execution_key, updated_data)

        # Primary key도 동일하게 업데이트
        primary_key = f"simulation:{simulation_id}"
        await self.redis.set(primary_key, updated_data)

        return {
            "_bytes_read": bytes_read,
            "_bytes_written": bytes_written * 2
        }


class PerformanceReporter:
    """성능 개선 리포트 생성"""

    @staticmethod
    def generate_report() -> str:
        """
        성능 측정 결과를 기반으로 개선 리포트 생성

        Returns:
            마크다운 형식의 성능 리포트
        """
        summary = get_metrics_summary()

        if not summary:
            return "# ⚠️ 측정된 메트릭이 없습니다"

        report_lines = [
            "# 🚀 Redis 업데이트 성능 개선 리포트\n",
            f"**측정 시각**: {datetime.now(timezone.utc).isoformat()}\n",
            "---\n"
        ]

        # 작업별 성능 요약
        report_lines.append("## 📊 작업별 성능 측정 결과\n")

        for operation, metrics in summary.items():
            report_lines.append(f"### {operation}\n")
            report_lines.append(f"- **호출 횟수**: {metrics['호출_횟수']:,}회")
            report_lines.append(f"- **총 소요시간**: {metrics['총_소요시간_ms']:,.2f} ms")
            report_lines.append(f"- **평균 소요시간**: {metrics['평균_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **최소 소요시간**: {metrics['최소_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **최대 소요시간**: {metrics['최대_소요시간_ms']:.2f} ms")
            report_lines.append(f"- **총 읽기 바이트**: {metrics['총_읽기_바이트']:,} bytes ({metrics['총_읽기_바이트'] / 1024:.2f} KB)")
            report_lines.append(f"- **평균 읽기 바이트**: {metrics['평균_읽기_바이트']:.2f} bytes")
            report_lines.append(f"- **총 쓰기 바이트**: {metrics['총_쓰기_바이트']:,} bytes ({metrics['총_쓰기_바이트'] / 1024:.2f} KB)")
            report_lines.append(f"- **평균 쓰기 바이트**: {metrics['평균_쓰기_바이트']:.2f} bytes\n")

        # 개선 효과 계산
        report_lines.append("## 💡 개선 효과 분석\n")

        # Sequential vs Parallel 비교
        seq_update = summary.get("sequential_step_update", {})
        par_update = summary.get("parallel_group_update", {})

        if seq_update and par_update:
            report_lines.append("### Sequential vs Parallel 업데이트 비교\n")
            report_lines.append(f"- Sequential 평균: {seq_update.get('평균_소요시간_ms', 0):.2f} ms")
            report_lines.append(f"- Parallel 평균: {par_update.get('평균_소요시간_ms', 0):.2f} ms")

            if seq_update.get('평균_소요시간_ms', 0) > par_update.get('평균_소요시간_ms', 0):
                improvement = ((seq_update['평균_소요시간_ms'] - par_update['평균_소요시간_ms'])
                               / seq_update['평균_소요시간_ms'] * 100)
                report_lines.append(f"- **Parallel이 {improvement:.1f}% 더 빠름**\n")
            else:
                improvement = ((par_update['평균_소요시간_ms'] - seq_update['평균_소요시간_ms'])
                               / par_update['평균_소요시간_ms'] * 100)
                report_lines.append(f"- **Sequential이 {improvement:.1f}% 더 빠름**\n")

        # 전체 통계
        total_operations = sum(m["호출_횟수"] for m in summary.values())
        total_time = sum(m["총_소요시간_ms"] for m in summary.values())
        total_read_kb = sum(m["총_읽기_바이트"] for m in summary.values()) / 1024
        total_write_kb = sum(m["총_쓰기_바이트"] for m in summary.values()) / 1024

        report_lines.append("### 전체 통계\n")
        report_lines.append(f"- **총 Redis 작업**: {total_operations:,}회")
        report_lines.append(f"- **총 소요시간**: {total_time:,.2f} ms ({total_time / 1000:.2f}초)")
        report_lines.append(f"- **총 데이터 읽기**: {total_read_kb:,.2f} KB")
        report_lines.append(f"- **총 데이터 쓰기**: {total_write_kb:,.2f} KB")
        report_lines.append(f"- **평균 작업 속도**: {total_time / max(total_operations, 1):.2f} ms/작업\n")

        # 개선 권장사항
        report_lines.append("## 🎯 개선 권장사항\n")

        # 느린 작업 식별
        slow_operations = [
            (op, m) for op, m in summary.items()
            if m.get("평균_소요시간_ms", 0) > 10  # 10ms 이상
        ]

        if slow_operations:
            report_lines.append("### ⚠️ 개선이 필요한 작업 (평균 10ms 이상)\n")
            for op, metrics in slow_operations:
                report_lines.append(
                    f"- **{op}**: {metrics['평균_소요시간_ms']:.2f} ms "
                    f"(호출 {metrics['호출_횟수']}회)"
                )
            report_lines.append("")

        # 데이터 전송량이 많은 작업
        heavy_operations = [
            (op, m) for op, m in summary.items()
            if m.get("평균_쓰기_바이트", 0) > 5000  # 5KB 이상
        ]

        if heavy_operations:
            report_lines.append("### 📦 데이터 전송량이 많은 작업 (평균 5KB 이상)\n")
            for op, metrics in heavy_operations:
                report_lines.append(
                    f"- **{op}**: {metrics['평균_쓰기_바이트'] / 1024:.2f} KB "
                    f"(호출 {metrics['호출_횟수']}회)"
                )
            report_lines.append("")

        report_lines.append("---\n")
        report_lines.append("*이 리포트는 자동 생성되었습니다*")

        return "\n".join(report_lines)
