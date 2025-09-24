from typing import List, Optional
from pydantic import ConfigDict, field_validator, Field

from .format import GlobalResponseModel
from settings import BaseSchema
from utils.my_enum import API


###### 생성 #######
class TemplateCreateRequest(BaseSchema):
    name: str = Field(examples=["LG사 로봇팔"])
    type: str = Field(examples=["robot-arm"])
    description: str = Field(examples=["This is robot-arm"])
    topics: str = Field(examples=["/navi_motion_traj, /nav_vel, /scan_unified"])
    bag_file_path: Optional[str] = Field(None, examples=["directory"])
    
    @field_validator("name", "type", "description", "topics", mode="before")
    def not_empty_string(cls, value):
        if not value or not value.strip():
            raise ValueError("빈 문자열 또는 공백만으로 구성된 값은 허용되지 않습니다.")
        return value
    
class TemplateFileInfo(BaseSchema):
    file_name: str
    download_url: str

class TemplateCreateResponse(BaseSchema):
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    template_id: int
    template_name: str
    template_type: str
    template_description: str
    topics: str
    created_at : str
    metadata_file: TemplateFileInfo
    db_file: TemplateFileInfo 

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

###### 수정 #######
class TemplateUpdateRequest(BaseSchema):
    name: Optional[str] = Field(None, examples=["LG사 로봇팔"])
    type: Optional[str] = Field(None, examples=["robot-arm"])
    description: Optional[str] = Field(None, examples=["This is robot-arm"])
    topics: Optional[str] = Field(None, examples=["/navi_motion_traj, /nav_vel, /scan_unified"])
    
    @field_validator("name", "type", "description", "topics", mode="before")
    def not_empty_string(cls, value):
        if value is not None:
            if not value.strip():  # 공백만 있는 경우도 체크
                raise ValueError("빈 문자열 또는 공백만으로 구성된 값은 허용되지 않습니다.")
        return value
    
class TemplateUpdateResponse(BaseSchema):
    template_id: int
    template_name: str
    template_type: str
    template_description: str
    topics: str
    created_at: str
    metadata_file: TemplateFileInfo
    db_file: TemplateFileInfo

class TemplateUpdateResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
            {
                "statusCode": 200,
                "data": {
                    "templateId": 1,
                    "templateName": "LG Robotic-Arm",
                    "templateType": "Robotic-Arm",
                    "templateDescription": "Updated robot arm description",
                    "topics": "/updated_cmd_vel, /updated_scan, /navi_local_path",
                    "createdAt": "2024-11-26 14:13:31.409721",
                    "metadataFile": {
                        "fileName": "metadata.yaml",
                        "downloadUrl": "http://127.0.0.1:9000/rosbag-data/robot-arm_20250910_052746/metadata.yaml"
                    },
                    "dbFile": {
                        "fileName": "robot-arm_20250910_052746.db3",
                        "downloadUrl": "http://127.0.0.1:9000/rosbag-data/robot-arm_20250910_052746/robot-arm_20250910_052746.db3"
                    }
                },
                "message": API.UPDATE_TEMPLATE.value,
            }
        }
    }
    pass

###### 목록 조회 #######    
class TemplateListResponse(BaseSchema):
    template_id: int
    template_name: str
    template_type: str
    template_description: str
    topics : str
    created_at : str
    metadata_file: TemplateFileInfo
    db_file: TemplateFileInfo 

class TemplateListResponseModel(GlobalResponseModel):
    model_config = {
        "json_schema_extra": {
            "example":
            {
                "statusCode": 200,
                "data": [
                    {
                        "templateId": 1,
                        "templateName": "LG Robotic-Arm",
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