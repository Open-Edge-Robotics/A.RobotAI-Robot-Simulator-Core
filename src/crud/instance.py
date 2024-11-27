from fastapi import HTTPException
from kubernetes import client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.models.instance import Instance, Pod
from src.models.simulation import Simulation
from src.models.template import Template
from src.schemas.instance import InstanceCreateRequest, InstanceCreateResponse, InstanceListResponse, \
    InstanceControlRequest, InstanceDetailResponse, InstanceControlResponse, InstanceDeleteResponse


class InstanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_instance(self, instance_create_data: InstanceCreateRequest):
        async with self.session.begin():
            # TODO: extract 할 수 있지 않을까? (시뮬id 검사, 템플릿id 검사)
            # 시뮬레이션 id 검사
            statement = select(Simulation).where(Simulation.id == instance_create_data.simulation_id)
            simulation = await self.session.scalar(statement)

            if simulation is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='존재하지 않는 시뮬레이션id 입니다.')

            # 템플릿 id 검사
            statement = select(Template).where(Template.template_id == instance_create_data.template_id)
            template = await self.session.scalar(statement)

            if template is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='존재하지 않는 템플릿id 입니다.')

            count = instance_create_data.instance_count
            new_instances = [
                Instance(
                name=instance_create_data.instance_name,
                description=instance_create_data.instance_description,
                template_id=template.template_id,
                template=template,
                ) for i in range(count)
            ]
            self.session.add_all(new_instances)

            await self.session.flush()
            for new_instance in new_instances:
                await self.session.refresh(new_instance)

            pod_sets = [
                Pod(
                    name = f"instance-{simulation.id}-{new_instance.id}",
                    instance=new_instance,
                    instance_id=new_instance.id,
                    simulation_id=simulation.id,
                    simulation= simulation
                ) for new_instance in new_instances
            ]

            self.session.add_all(pod_sets)

            await self.session.flush()
            for new_pod in pod_sets:
                await self.session.refresh(new_pod)

            return [
                InstanceCreateResponse(
                    instance_id=new_pod.instance_id,
                    instance_name=new_pod.instance.name,
                    instance_description=new_pod.instance.description,
                    template_id=new_pod.instance.template_id,
                    simulation_id=new_pod.simulation_id,
                    pod_name=new_pod.name,
                )
                for new_pod in pod_sets
            ]

    async def create_pod(self, instance_id, instance_create_data):
        statement = select(Template).where(Template.template_id == instance_create_data.template_id)
        template = await self.session.scalar(statement)

        pod_client = client.CoreV1Api()
        for i in range(instance_create_data.instance_count):
            pod_name = f"instance-{instance_id}-{i + 1}"
            pod_metadata = client.V1ObjectMeta(name=pod_name)
            pod_env = client.V1EnvVar(name="AGENT_TYPE", value=template.type)

            container = client.V1Container(
                name=pod_name,
                image="shis1008/pod:latest",
                env=pod_env,
            )
            pod = client.V1Pod(
                metadata=pod_metadata,
                spec=client.V1PodSpec(containers=[container]),
            )

            pod_client.create_namespaced_pod(namespace="robot", body=pod)

    async def get_all_instances(self):
        try:
            statement = (
                select(Instance).
                order_by(Instance.id.desc())
            )
            results = await self.session.scalars(statement)

            instance_list = [
                InstanceListResponse(
                    instance_id=instance.id,
                    instance_name=instance.name,
                    instance_description=instance.description,
                    instance_created_at=str(instance.created_at)
                )
                for instance in results.all()
            ]

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='인스턴스 목록 조회 실패: ' + str(e))

        return instance_list

    async def get_instance(self, instance_id: int):
        # 추후 연동 시 수동 데이터 수정 및 로직 추가

        return InstanceDetailResponse(
            instance_id=instance_id,
            instance_namespace="instanceNamespace",
            instance_port_number=3000,
            instance_age="20d",
            template_type="templateType",
            instance_volume="instanceVolume",
            instance_log="instanceLog",
            instance_status="instanceStatus",
            topics="topics",
        ).model_dump()

    async def control_instance(self, instance_control_data: InstanceControlRequest):
        # 추후 연동 시 로직 추가
        instance_id = instance_control_data.instance_id
        action = instance_control_data.action

        return InstanceControlResponse(
            instance_id=instance_control_data.instance_id
        ).model_dump(), action

    async def delete_instance(self, instance_id: int):
        # 추후 연동 시 수동 데이터 수정

        return InstanceDeleteResponse(
            instance_id=instance_id
        ).model_dump()
