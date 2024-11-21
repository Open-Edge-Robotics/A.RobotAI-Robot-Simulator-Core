from typing import Dict

from src.settings import BaseSchema


# 상세 조회용
class InstanceDetailResponse(BaseSchema):
    instance_namespace: str
    instance_port_number: str
    instance_age: str
    template_type: str
    instance_volume: str
    instance_log: str
    instance_status: str
    topics: str

# 목록 조회용
class InstanceBriefResponse(BaseSchema):
    instance_id: str
    instance_name: str
    instance_description: str
    instance_created_at: str

# 인스턴스 생성에 대한 response
class InstanceCreationResponse(BaseSchema):
    instance_name : str
    instance_description : str
    simulation_id: str
    template_id: str
    instance_count: str

class InstanceCreateModel(BaseSchema):
    name: str
    description: str
    templateSet: Dict[int, int] # 템플릿 이름, 개수

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "instance1",
                "description": "instance1 입니다~~",
                "templateSet": {1: 20, 2: 30, 3: 10}
            }
        }
    }


