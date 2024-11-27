from src.settings import BaseSchema

from src.schemas.format import GlobalResponseModel


###### 생성 #######
class InstanceCreateRequest(BaseSchema):
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

class InstanceCreateResponse(BaseSchema):
    instance_id: int
    instance_name : str
    instance_description : str
    simulation_id: int
    template_id: int
    pod_name : str

class InstanceCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "statusCode": 201,
                    "data": [
                        {
                            "instanceId": 1,
                            "instanceName": "instance1",
                            "instanceDescription": "instance1 입니다~~",
                            "templateId": 2,
                            "simulationId": 1,
                            "podName": "instance-1-1"
                        }
                    ],
                    "message": "인스턴스 생성 성공"
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

class InstanceListResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "statusCode": 200,
                    "data": [
                        {
                            "instanceId": 1,
                            "instanceName": "instance1",
                            "instanceDescription": "instance1 입니다~~",
                            "instanceCreatedAt": "2024-11-22 08:22:16.315731"
                        }
                    ],
                    "message": "인스턴스 목록 조회 성공"
                }
        }
    }

    pass


###### 상세 조회 #######
class InstanceDetailResponse(BaseSchema):
    instance_id: int
    instance_namespace: str
    instance_port_number: int
    instance_age: str
    template_type: str
    instance_volume: str
    instance_status: str
    topics: str

class InstanceDetailResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "statusCode": 200,
                    "data": {
                        "instanceId": 1,
                        "instanceNamespace": "robot",
                        "instancePortNumber": 3000,
                        "instanceAge": "20d",
                        "templateType": "templateType",
                        "instanceVolume": "instanceVolume",
                        "instanceLog": "instanceLog",
                        "instanceStatus": "instanceStatus",
                        "topics": "topics"
                    },
                    "message": "인스턴스 상세 조회 성공"
                }
        }
    }

    pass


###### 실행 #######
class InstanceControlRequest(BaseSchema):
    instance_id: int
    action: str

    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "instanceId": 1,
                    "action": "start"
                }
        }
    }

class InstanceControlResponse(BaseSchema):
    instance_id: int

class InstanceControlResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
                {
                    "instanceId": 1
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
            "example":
                {
                    "instanceId": 1
                }
        }
    }

    pass
