from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette import status

from src.crud.pod import PodService
from src.crud.rosbag import RosService
from src.crud.simulation import SimulationService
from src.crud.template import TemplateService
from src.models.instance import Instance
from src.schemas.instance import *
from src.utils.my_enum import API, PodStatus, InstanceStatus


class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.simulation_service = SimulationService(session)
        self.templates_service = TemplateService(session)
        self.pod_service = PodService()
        self.ros_service = RosService()

    async def create_instance(self, instance_create_data: InstanceCreateRequest):
        api = API.CREATE_INSTANCE.value

        async with (self.session.begin()):
            simulation = await self.simulation_service.find_simulation_by_id(instance_create_data.simulation_id, api)
            template = await self.templates_service.find_template_by_id(instance_create_data.template_id, api)

            count = instance_create_data.instance_count
            new_instances = [
                Instance(
                    name=instance_create_data.instance_name,
                    description=instance_create_data.instance_description,
                    pod_namespace=simulation.namespace,
                    template_id=template.template_id,
                    template=template,
                    simulation_id=simulation.id,
                    simulation=simulation
                ) for _ in range(count)
            ]
            self.session.add_all(new_instances)

            await self.session.flush()
            for instance in new_instances:
                await self.session.refresh(instance)
                instance.pod_name = await self.pod_service.create_pod(instance, template)
                self.session.add(instance)

            return [
                InstanceCreateResponse(
                    instance_id=new_instance.id,
                    instance_name=new_instance.name,
                    instance_description=new_instance.description,
                    template_id=new_instance.template_id,
                    simulation_id=new_instance.simulation_id,
                    pod_name=new_instance.pod_name,
                )
                for new_instance in new_instances
            ]

    async def get_all_instances(self, simulation_id: Optional[int]):
        if simulation_id is None:
            result = await self.session.execute(select(Instance).order_by(Instance.id.desc()))
        else:
            simulation = await self.simulation_service.find_simulation_by_id(simulation_id, API.GET_INSTANCES.value)
            query = (
                select(Instance)
                .where(Instance.simulation_id == simulation.id)
                .order_by(Instance.id.desc())
            )
            result = await self.session.execute(query)

        instances = result.scalars().all()
        instance_list = []

        for instance in instances:
            pod_name = instance.pod_name
            pod_namespace = instance.pod_namespace

            response = InstanceListResponse(
                instance_id=instance.id,
                instance_name=instance.name,
                instance_description=instance.description,
                instance_created_at=str(instance.created_at),
                pod_name=pod_name,
                pod_namespace=pod_namespace,
                pod_status=await self.get_instance_status(pod_name, pod_namespace),
            )
            instance_list.append(response)

        return instance_list

    async def get_instance(self, instance_id: int):
        instance = await self.find_instance_by_id(instance_id, API.GET_INSTANCE.value)
        pod_name = instance.pod_name
        namespace = instance.pod_namespace

        return InstanceDetailResponse(
            instance_id=instance.id,
            pod_name=pod_name,
            instance_namespace=namespace,
            instance_status=await self.get_instance_status(pod_name, namespace),
            instance_image=await self.pod_service.get_pod_image(pod_name, namespace),
            instance_age=await self.pod_service.get_pod_age(pod_name, namespace),
            instance_label=await self.pod_service.get_pod_label(pod_name, namespace),
            template_type=instance.template.type,
            topics=instance.template.topics,
        ).model_dump()

    async def delete_instance(self, instance_id: int):
        find_instance = await self.find_instance_by_id(instance_id, API.DELETE_INSTANCE.value)

        await self.pod_service.delete_pod(
            find_instance.pod_name,
            find_instance.pod_namespace
        )

        await self.session.delete(find_instance)
        await self.session.commit()

        return InstanceDeleteResponse(
            instance_id=find_instance.id
        ).model_dump()

    async def start_instances(self, instance_ids: List[int]):
        for instance_id in instance_ids:
            instance = await self.find_instance_by_id(instance_id, "start instance")
            await self.pod_service.check_pod_status(instance)
            object_path = instance.template.bag_file_path
            pod_ip = await self.pod_service.get_pod_ip(instance)
            await self.ros_service.send_post_request(pod_ip, "/rosbag/play", {"object_path": object_path})
        return InstanceControlResponse(status="START").model_dump()

    async def stop_instances(self, instance_ids: List[int]):
        for instance_id in instance_ids:
            instance = await self.find_instance_by_id(instance_id, "stop instance")
            pod_ip = await self.pod_service.get_pod_ip(instance)
            await self.ros_service.send_post_request(pod_ip, "/rosbag/stop")
        return InstanceControlResponse(status="STOP").model_dump()

    async def check_instance_status(self, instance_ids: List[int]):
        status_list = []
        for instance_id in instance_ids:
            instance = await self.find_instance_by_id(instance_id, "check instance")
            pod_ip = await self.pod_service.get_pod_ip(instance)
            pod_status = await self.ros_service.send_get_request(pod_ip)

            status_response = InstanceStatusResponse(
                instance_id=instance.id,
                running_status=pod_status,
            )
            status_list.append(status_response)

        return status_list

    async def get_instance_status(self, pod_name, namespace):
        pod_status = await self.pod_service.get_pod_status(pod_name, namespace)
        if pod_status == PodStatus.RUNNING.value:
            return InstanceStatus.READY.value
        return pod_status

    async def find_instance_by_id(self, instance_id: int, api: str):
        query = (
            select(Instance).
            where(Instance.id == instance_id).
            options(joinedload(Instance.template))
        )
        result = await self.session.execute(query)
        instance = result.scalar_one_or_none()

        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{api}: 존재하지 않는 인스턴스id 입니다.')
        return instance

    async def get_instances_by_ids(self, instance_ids: List[int]) -> List[Instance]:
        query = (
            select(Instance).
            where(Instance.id.in_(instance_ids)).
            options(joinedload(Instance.template), joinedload(Instance.simulation))
        )
        result = await self.session.execute(query)
        instances = result.scalars().all()
        return list(instances)
