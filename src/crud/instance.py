import os
import subprocess

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
from src.schemas.instance import InstanceCreateRequest, InstanceCreateResponse, InstanceListResponse, \
    InstanceDetailResponse, InstanceDeleteResponse, InstanceListRequest

pod_service = PodService()


class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.simulation_service = SimulationService(session)
        self.templates_service = TemplateService(session)

    async def create_instance(self, instance_create_data: InstanceCreateRequest):
        async with (self.session.begin()):
            simulation = await self.simulation_service.find_simulation_by_id(instance_create_data.simulation_id,
                                                                             "인스턴스 생성")
            template = await self.templates_service.find_template_by_id(instance_create_data.template_id, "인스턴스 생성")

            count = instance_create_data.instance_count
            new_instances = [
                Instance(
                    name=instance_create_data.instance_name,
                    description=instance_create_data.instance_description,
                    pod_namespace=instance_create_data.pod_namespace,
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

    async def get_all_instances(self, request: InstanceListRequest):
        simulation_id = request.simulation_id

        if simulation_id is None:
            result = await self.session.execute(select(Instance))
        else:
            simulation = await self.simulation_service.find_simulation_by_id(simulation_id, "시뮬레이션의 인스턴스 목록 조회")
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
                pod_status=await pod_service.get_pod_status(pod_name, pod_namespace),
            )
            instance_list.append(response)

        return instance_list

    async def get_instance(self, instance_id: int):
        instance = await self.find_instance_by_id(instance_id, '인스턴스 상세 조회')
        pod_name = instance.pod_name
        namespace = instance.pod_namespace

        return InstanceDetailResponse(
            instance_id=instance.id,
            pod_name=pod_name,
            instance_namespace=namespace,
            instance_status=await pod_service.get_pod_status(pod_name, namespace),
            instance_image=await pod_service.get_pod_image(pod_name, namespace),
            instance_age=await pod_service.get_pod_age(pod_name, namespace),
            instance_label=await pod_service.get_pod_label(pod_name, namespace),
            template_type=instance.template.type,
            topics=instance.template.topics,
        )

    async def find_instance_by_id(self, instance_id: int, api: str):
        try:
            query = (
                select(Instance).
                where(Instance.id == instance_id).
                options(joinedload(Instance.template))
            )
            result = await self.session.execute(query)
            instance = result.scalar_one_or_none()

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f'{api} 실패 : 데이터베이스 조회 중 오류가 발생했습니다. : {str(e)}')

        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{api} 실패 : 존재하지 않는 인스턴스id 입니다.')
        return instance
