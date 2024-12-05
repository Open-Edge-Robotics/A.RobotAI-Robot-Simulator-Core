import os
import subprocess
from typing import Optional

from fastapi import HTTPException
from minio import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette import status

from src.crud.pod import PodService
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

    async def get_instance_status(self, pod_name, namespace):
        pod_status = await pod_service.get_pod_status(pod_name, namespace)
        if pod_status == PodStatus.RUNNING.value:
            return InstanceStatus.READY.value
        return pod_status

    async def control_instance(self, instance_id: int):
        file_path = await self.download_bag_file(instance_id)

        try:
            command = ['ros2', 'bag', 'play', str(file_path)]
            subprocess.run(command, check=True)
            print(f"Successfully started playing rosbag: {file_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error occurred while playing rosbag: {e}")

        return InstanceControlResponse(status="START").model_dump()

    async def get_bag_file_path(self, instance_id: int):
        instance = await self.find_instance_by_id(instance_id, "다운로드 백파일")
        template = instance.template
        file_path = os.path.join("/rosbag-data", template.bag_file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if os.path.exists(file_path):
            return file_path
        else:
            return await self.download_bag_file(file_path, template)

    async def download_bag_file(self, file_path, template):
        try:
            minio_client = minio_conn.client
            minio_client.fget_object(
                bucket_name=minio_conn.bucket_name,
                object_name=template.bag_file_path,
                file_path=file_path
            )
            return file_path
        except S3Error as e:
            print(f"Error downloading bag file: {e}")
        return None
