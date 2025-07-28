import asyncio
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette import status
from .template import TemplateService

from .pod import PodService
from .rosbag import RosService
from models.instance import Instance
from models.simulation import Simulation
from schemas.simulation import (
    SimulationCreateRequest, SimulationListResponse, SimulationCreateResponse,
    SimulationDeleteResponse, SimulationControlResponse, SimulationPatternUpdateRequest,
    SimulationPatternUpdateResponse
)
from utils.my_enum import SimulationStatus, PodStatus, API


class SimulationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.ros_service = RosService()
        self.pod_service = PodService()
        self.templates_service = TemplateService(session)

    async def create_simulation(self, simulation_create_data: SimulationCreateRequest):
        api = API.CREATE_INSTANCE.value

        async with (self.session.begin()):
            # 시뮬레이션 이름 중복 검사
            statement = select(
                exists().
                where(Simulation.name == simulation_create_data.simulation_name)
            )
            is_existed = await self.session.scalar(statement)

            if is_existed:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail=f"{API.CREATE_SIMULATION.value}: 시뮬레이션 이름이 이미 존재합니다.")

            # 스케줄 시간 검증
            if (simulation_create_data.scheduled_start_time and
                    simulation_create_data.scheduled_end_time and
                    simulation_create_data.scheduled_start_time >= simulation_create_data.scheduled_end_time):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"{API.CREATE_SIMULATION.value}: 종료 시간은 시작 시간보다 늦어야 합니다.")

            template = await self.templates_service.find_template_by_id(simulation_create_data.template_id, api)
            new_simulation = Simulation(
                name=simulation_create_data.simulation_name,
                description=simulation_create_data.simulation_description,
                template_id=template.template_id,
                template=template,
                autonomous_agent_count=simulation_create_data.autonomous_agent_count,
                execution_time=simulation_create_data.execution_time,
                delay_time=simulation_create_data.delay_time,
                repeat_count=simulation_create_data.repeat_count,
                scheduled_start_time=simulation_create_data.scheduled_start_time,
                scheduled_end_time=simulation_create_data.scheduled_end_time,
                mec_id=simulation_create_data.mec_id
            )

            self.session.add(new_simulation)
            await self.session.flush()

            # namespace 생성
            simulation_namespace = await self.pod_service.create_namespace(new_simulation.id)
            new_simulation.namespace = simulation_namespace

            agent_instances = []

            repeat_count = simulation_create_data.repeat_count
            agent_count = simulation_create_data.autonomous_agent_count
            delay_time = simulation_create_data.delay_time  # 단위: 초

            if repeat_count > 0:
                for repeat_idx in range(repeat_count):
                    current_batch_instances = []
                    for agent_idx in range(agent_count):
                        instance = Instance(
                            name=f"{new_simulation.name}_agent_{repeat_idx}_{agent_idx}",
                            description=f"Repeat {repeat_idx} - Autonomous agent {agent_idx} for simulation {new_simulation.name}",
                            pod_namespace=simulation_namespace,
                            template_id=template.template_id,
                            template=template,
                            simulation_id=new_simulation.id,
                            simulation=new_simulation
                        )
                        current_batch_instances.append(instance)

                    self.session.add_all(current_batch_instances)
                    await self.session.flush()

                    for instance in current_batch_instances:
                        instance.pod_name = await self.pod_service.create_pod(instance, template)
                        self.session.add(instance)

                    # 반복 사이에 delay
                    if repeat_idx < repeat_count - 1 and delay_time > 0:
                        await asyncio.sleep(delay_time)  # 💡 지정된 초 단위로 대기
            else:
                for agent_idx in range(agent_count):
                    instance = Instance(
                        name=f"{new_simulation.name}_agent_{agent_idx}",
                        description=f"Autonomous agent {agent_idx} for simulation {new_simulation.name}",
                        pod_namespace=simulation_namespace,
                        template_id=template.template_id,
                        template=template,
                        simulation_id=new_simulation.id,
                        simulation=new_simulation
                    )
                    agent_instances.append(instance)

            self.session.add_all(agent_instances)
            await self.session.flush()

            for instance in agent_instances:
                instance.pod_name = await self.pod_service.create_pod(instance, template)
                self.session.add(instance)

            await self.session.refresh(new_simulation)

            return SimulationCreateResponse(
                simulation_id=new_simulation.id,
                simulation_name=new_simulation.name,
                simulation_description=new_simulation.description,
                simulation_namespace=new_simulation.namespace,
                template_id=new_simulation.template_id,
                autonomous_agent_count=new_simulation.autonomous_agent_count,
                execution_time=new_simulation.execution_time,
                delay_time=new_simulation.delay_time,
                repeat_count=new_simulation.repeat_count,
                scheduled_start_time=str(
                    new_simulation.scheduled_start_time) if new_simulation.scheduled_start_time else None,
                scheduled_end_time=str(
                    new_simulation.scheduled_end_time) if new_simulation.scheduled_end_time else None,
                mec_id=new_simulation.mec_id
            ).model_dump()

    async def get_all_simulations(self):
        statement = (
            select(Simulation).
            options(selectinload(Simulation.instances)).
            order_by(Simulation.id.desc())
        )
        results = await self.session.execute(statement)
        simulations = results.scalars().all()

        simulation_list = []

        for simulation in simulations:
            simulation_status = await self.get_simulation_status(simulation)

            response = SimulationListResponse(
                simulation_id=simulation.id,
                simulation_name=simulation.name,
                simulation_description=simulation.description,
                simulation_namespace=simulation.namespace,
                simulation_created_at=str(simulation.created_at),
                simulation_status=simulation_status,
                template_id=simulation.template_id,
                autonomous_agent_count=simulation.autonomous_agent_count,
                execution_time=simulation.execution_time,
                delay_time=simulation.delay_time,
                repeat_count=simulation.repeat_count,
                scheduled_start_time=str(simulation.scheduled_start_time) if simulation.scheduled_start_time else None,
                scheduled_end_time=str(simulation.scheduled_end_time) if simulation.scheduled_end_time else None,
                mec_id=simulation.mec_id
            )
            simulation_list.append(response)

        return simulation_list

    async def update_simulation_pattern(self, simulation_id: int, pattern_data: SimulationPatternUpdateRequest):
        """시뮬레이션 패턴 설정 업데이트"""
        simulation = await self.find_simulation_by_id(simulation_id, "update simulation pattern")

        # 시뮬레이션이 실행 중인지 확인
        current_status = await self.get_simulation_status(simulation)
        if current_status == SimulationStatus.ACTIVE.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="실행 중인 시뮬레이션의 패턴은 수정할 수 없습니다.")

        # 스케줄 시간 검증
        if (pattern_data.scheduled_start_time and
                pattern_data.scheduled_end_time and
                pattern_data.scheduled_start_time >= pattern_data.scheduled_end_time):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="종료 시간은 시작 시간보다 늦어야 합니다.")

        # 업데이트할 필드들 준비
        update_data = {}
        for field, value in pattern_data.model_dump(exclude_unset=True).items():
            update_data[field] = value

        if update_data:
            update_data['updated_at'] = datetime.now()

            statement = (
                update(Simulation)
                .where(Simulation.id == simulation_id)
                .values(**update_data)
            )
            await self.session.execute(statement)
            await self.session.commit()

        return SimulationPatternUpdateResponse(
            simulation_id=simulation_id,
            message="패턴 설정이 성공적으로 업데이트되었습니다"
        ).model_dump()

    async def start_simulation(self, simulation_id: int):
        simulation = await self.find_simulation_by_id(simulation_id, "start simulation")

        # 스케줄된 시작 시간 확인
        if simulation.scheduled_start_time:
            current_time = datetime.now()
            if current_time < simulation.scheduled_start_time:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"시뮬레이션은 {simulation.scheduled_start_time}에 시작할 수 있습니다.")

        # 스케줄된 종료 시간 확인
        if simulation.scheduled_end_time:
            current_time = datetime.now()
            if current_time > simulation.scheduled_end_time:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"시뮬레이션의 예정 종료 시간({simulation.scheduled_end_time})이 지났습니다.")

        instances = await self.get_simulation_instances(simulation_id)

        for instance in instances:
            object_path = instance.template.bag_file_path
            await self.pod_service.check_pod_status(instance)
            pod_ip = await self.pod_service.get_pod_ip(instance)

            # 고도화된 rosbag 실행 파라미터 준비
            rosbag_params = {
                "object_path": object_path,
                "max_loops": simulation.repeat_count,
                "delay_between_loops": simulation.delay_time or 0,
                "execution_duration": simulation.execution_time
            }

            await self.ros_service.send_post_request(pod_ip, "/rosbag/play", rosbag_params)

        return SimulationControlResponse(simulation_id=simulation_id).model_dump()

    async def stop_simulation(self, simulation_id: int):
        instances = await self.get_simulation_instances(simulation_id)
        for instance in instances:
            await self.pod_service.check_pod_status(instance)
            pod_ip = await self.pod_service.get_pod_ip(instance)
            await self.ros_service.send_post_request(pod_ip, "/rosbag/stop")

        return SimulationControlResponse(simulation_id=simulation_id).model_dump()

    async def get_simulation_instances(self, simulation_id: int):
        simulation = await self.find_simulation_by_id(simulation_id, "control simulation")
        query = (
            select(Instance)
            .options(joinedload(Instance.template))
            .where(Instance.simulation_id == simulation.id)
        )
        result = await self.session.execute(query)
        instances = result.scalars().all()
        return list(instances)

    async def delete_simulation(self, simulation_id: int):
        api = API.DELETE_SIMULATION.value
        simulation = await self.find_simulation_by_id(simulation_id, api)

        # 시뮬레이션이 실행 중인지 확인
        current_status = await self.get_simulation_status(simulation)
        if current_status == SimulationStatus.ACTIVE.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f'{api}: 실행 중인 시뮬레이션은 삭제할 수 없습니다.')

        # 시뮬레이션이 존재해야 아래 코드 실행됨
        statement = select(
            exists().
            where(Instance.simulation_id == simulation_id)
        )
        is_existed = await self.session.scalar(statement)

        if is_existed is False:
            await self.session.delete(simulation)
            await self.session.commit()

            await self.pod_service.delete_namespace(simulation_id)
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f'{api}: 삭제하려는 시뮬레이션에 속한 인스턴스가 있어 시뮬레이션 삭제가 불가합니다.')

        return SimulationDeleteResponse(
            simulation_id=simulation_id
        ).model_dump()

    async def find_simulation_by_id(self, simulation_id: int, api: str):
        query = (
            select(Simulation)
            .options(selectinload(Simulation.instances))
            .where(Simulation.id == simulation_id)
        )
        result = await self.session.execute(query)
        simulation = result.scalar_one_or_none()

        if not simulation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f'{api}: 존재하지 않는 시뮬레이션id 입니다.')
        return simulation

    async def get_simulation_status(self, simulation):
        instances = simulation.instances

        if not instances:
            return SimulationStatus.EMPTY.value

        for instance in instances:
            pod_ip = await self.pod_service.get_pod_ip(instance)
            pod_status = await self.ros_service.send_get_request(pod_ip)

            if pod_status == PodStatus.RUNNING.value:
                return SimulationStatus.ACTIVE.value

        return SimulationStatus.INACTIVE.value

    async def get_simulation_detailed_status(self, simulation_id: int):
        """시뮬레이션의 상세 상태 정보 반환"""
        simulation = await self.find_simulation_by_id(simulation_id, "get simulation status")
        instances = await self.get_simulation_instances(simulation_id)

        if not instances:
            return {"status": "EMPTY", "message": "인스턴스가 없습니다"}

        detailed_status = []
        for instance in instances:
            try:
                pod_ip = await self.pod_service.get_pod_ip(instance)
                status_response = await self.ros_service.send_get_request(pod_ip, "/rosbag/status")
                detailed_status.append({
                    "instance_id": instance.id,
                    "pod_ip": pod_ip,
                    "status": status_response
                })
            except Exception as e:
                detailed_status.append({
                    "instance_id": instance.id,
                    "error": str(e)
                })

        return {
            "simulation_id": simulation_id,
            "simulation_name": simulation.name,
            "instances_status": detailed_status
        }