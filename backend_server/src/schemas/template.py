from pydantic import ConfigDict, field_validator, Field

from .format import GlobalResponseModel
from settings import BaseSchema
from utils.my_enum import API


###### 생성 #######
class TemplateCreateRequest(BaseSchema):
    type: str = Field(examples=["robot-arm"])
    description: str = Field(examples=["This is robot-arm"])
    bag_file_path: str = Field(examples=["bagfiles/blahblah.db3"])
    topics: str = Field(examples=["/navi_motion_traj, /nav_vel, /scan_unified"])

class TemplateCreateResponse(BaseSchema):
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    template_id: int
    type: str
    description: str
    bag_file_path: str
    topics: str
    created_at : str

    @field_validator('created_at', mode='before')
    def format_datetime(cls, value):
        return str(value)

class TemplateCreateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
            {
                "statusCode": 201,
                "data": {
                    "templateId": 1,
                    "type": "A",
                    "description": "템플릿A 입니다~~~",
                    "bagFilePath": "blah/blah/blah",
                    "topics": "topics",
                    "createdAt": "2024-11-26 14:13:31.409721"
                },
                "message": API.CREATE_TEMPLATE.value,
            }
        }
    }
    pass


###### 목록 조회 #######
class TemplateListResponse(BaseSchema):
    template_id: int
    template_type: str
    template_description: str
    topics : str
    created_at : str

class TemplateListResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
            {
                "statusCode": 200,
                "data": [
                    {
                        "templateId": 1,
                        "templateType": "Robotic-Arm",
                        "templateDescription": "This is robot arm",
                        "topics" : "/cmd_vel, /scan, /navi_local_path",
                        "created_at" : "2024-11-26 14:13:31.409721",
                    }
                ],
                "message": API.GET_TEMPLATES.value,
            }
        }
    }

    pass


###### 삭제 #######
class TemplateDeleteResponse(BaseSchema):
    template_id: int

class TemplateDeleteResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
            {
                "statusCode": 200,
                "data": {
                    "templateId": 1
                },
                "message": API.DELETE_TEMPLATE.value,
            }
        }
    }
    pass