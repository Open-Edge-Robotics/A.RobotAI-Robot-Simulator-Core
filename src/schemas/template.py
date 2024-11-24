from pydantic import ConfigDict, field_validator

from src.settings import BaseSchema


class TemplateListResponse(BaseSchema):
    template_id: str
    template_type: str
    template_description: str

class TemplateCreateRequest(BaseSchema):
    type: str
    description: str
    bag_file_path: str
    topics: str

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

class TemplateDeleteResponse(BaseSchema):
    result: str
    template_id: int