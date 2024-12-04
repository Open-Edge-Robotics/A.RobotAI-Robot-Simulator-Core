from typing import List

from pydantic import Field

from src.schemas.format import GlobalResponseModel
from src.settings import BaseSchema
from src.utils.my_enum import API


###### 생성 #######
class InstanceCreateRequest(BaseSchema):
    instance_name: str = Field(examples=["instance1"])
    instance_description: str = Field(examples=["instance1 입니다~~"])
    simulation_id: int = Field(examples=[1])
    template_id: int = Field(examples=[2])
    instance_count: int = Field(examples=[10])
    pod_namespace: str = Field(examples=["instance"])

class InstanceCreateResponse(BaseSchema):
    instance_id: int
    instance_name: str
    instance_description: str
    simulation_id: int
    template_id: int
    pod_name: str

class InstanceCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 201,
                "data": [
                    {
                        "instanceId": 1,
                        "instanceName": "instance1",
                        "instanceDescription": "instance1 입니다~~",
                        "templateId": 2,
                        "simulationId": 3,
                        "podName": "instance-3-1"
                    }
                ],
                "message": API.CREATE_INSTANCE.value
            }
        }
    }

    pass


###### 목록 조회 #######
class InstanceListResponse(BaseSchema):
    instance_id: int
    instance_name: str
    instance_description: str
    instance_created_at: str
    pod_name: str
    pod_namespace: str
    pod_status: str

class InstanceListResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": [
                    {
                        "instanceId": 1,
                        "instanceName": "instance1",
                        "instanceDescription": "instance1 입니다~~",
                        "instanceCreatedAt": "2024-11-22 08:22:16.315731",
                        "podName": "instance-3-1",
                        "podNamespace": "instance",
                        "podStatus": "Running"
                    }
                ],
                "message": API.GET_TEMPLATES.value
            }
        }
    }

    pass


###### 상세 조회 #######
class InstanceDetailResponse(BaseSchema):
    instance_id: int
    pod_name: str
    instance_namespace: str
    instance_status: str
    instance_image: str
    instance_age: str
    instance_label: str
    template_type: str
    topics: str

class InstanceDetailResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":{
                "statusCode": 200,
                "data": {
                    "instanceId": 1,
                    "podName": "instance-3-1",
                    "instanceNamespace": "instance",
                    "instanceStatus": "Running",
                    "instanceImage": "shis1008/pod:latest",
                    "instanceAge": "239min",
                    "instanceLabel": "robot-arm",
                    "templateType": "robot-arm",
                    "topics": "/navi_motion_traj, /nav_vel, /scan_unified"
                },
                "message": API.GET_INSTANCE.value
            }
        }
    }

    pass


###### 실행 #######
class InstanceControlRequest(BaseSchema):
    instance_ids: List[int]
    action: str = Field(examples=["start"]) # TODO: openapi_examples로 start/stop 시나리오 표현 가능

class InstanceControlResponse(BaseSchema):
    status: str

class InstanceControlResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {
                    "instanceId": 1,
                },
                "message": API.RUN_INSTANCE.value
            }
        }
    }

    pass


###### 삭제 #######
class InstanceDeleteResponse(BaseSchema):
    instance_id: int

class InstanceDeleteResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "statusCode": 200,
                "data": {
                    "instanceId": 1,
                },
                "message": API.DELETE_INSTANCE.value
            }
        }
    }

    pass
