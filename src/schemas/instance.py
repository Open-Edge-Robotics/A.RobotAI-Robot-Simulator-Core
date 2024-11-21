from src.settings import BaseSchema

from src.schemas.format import GlobalResponseModel

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

# 인스턴스 생성
class InstanceCreateModel(BaseSchema):
    instance_name : str
    instance_description : str
    simulation_id: int
    template_id: int
    instance_count: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "instanceName": "instance1",
                "instanceDescription": "instance1 입니다~~",
                "simulationId": 1,
                "templateId": 2,
                "instanceCount": 10
            }
        }
    }

# 인스턴스 생성 시 반환 필드
# TODO: 이름 규칙 정하기
class InstanceCreateResponse(BaseSchema):
    instance_id: int
    instance_name : str
    instance_description : str

    def model_dump(self):
        return super().model_dump(by_alias=True)

class InstanceCreateResponseModel(GlobalResponseModel):
    #TODO: 예시 작성하기
    pass
