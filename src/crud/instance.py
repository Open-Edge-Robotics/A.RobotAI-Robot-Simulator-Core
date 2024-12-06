import os
from typing import Optional, Sequence

import requests
from fastapi import HTTPException
from kubernetes import config, client
from minio import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette import status

from src.crud.pod import PodService
from src.crud.rosbag import RosService
from src.crud.simulation import SimulationService
from src.crud.template import TemplateService
from src.database import minio_conn
from src.models.instance import Instance
from src.schemas.instance import *
from src.utils.my_enum import API, PodStatus, InstanceStatus

pod_service = PodService()


class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.simulation_service = SimulationService(session)
        self.templates_service = TemplateService(session)
        self.ros_service = RosService(session)

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
                instance.pod_name = await pod_service.create_pod(instance, template)
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
            instance_image=await pod_service.get_pod_image(pod_name, namespace),
            instance_age=await pod_service.get_pod_age(pod_name, namespace),
            instance_label=await pod_service.get_pod_label(pod_name, namespace),
            template_type=instance.template.type,
            topics=instance.template.topics,
        ).model_dump()

    async def delete_instance(self, instance_id: int):
        find_instance = await self.find_instance_by_id(instance_id, API.DELETE_INSTANCE.value)

        await pod_service.delete_pod(
            find_instance.pod_name,
            find_instance.pod_namespace
        )

        await self.session.delete(find_instance)
        await self.session.commit()

        return InstanceDeleteResponse(
            instance_id=find_instance.id
        ).model_dump()


    async def control_instance(self, instance_ids: List[int]):
        instances = await self.get_instances_by_ids(instance_ids)
        await self.ros_service.run_instances(instances)

        return InstanceControlResponse(status="START").model_dump()

    async def control_instance_temp(self, instance_ids: List[int]):
        config.load_kube_config('/root/.kube/config')
        pod_client = client.CoreV1Api()

        for instance_id in instance_ids:
            instance = await self.find_instance_by_id(instance_id, "control instance")
            object_path = instance.template.bag_file_path

            pod_name = f"instance-{instance.simulation_id}-{instance.id}"
            pod = pod_client.read_namespaced_pod(name=pod_name, namespace=instance.pod_namespace)

            pod_ip = pod.status.pod_ip
            pod_api_url = f"http://{pod_ip}:8002/download"
            print(requests.get(f"http://{pod_ip}:8002/"))

            requests.post(pod_api_url, json={"object_path": object_path})

        return InstanceControlResponse(status="START").model_dump()

    async def get_instance_status(self, pod_name, namespace):
        pod_status = await pod_service.get_pod_status(pod_name, namespace)
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
        result = await self.session.execute(
            select(Instance).where(Instance.id.in_(instance_ids))
        )
        instances = result.scalars().all()
        return list(instances)
