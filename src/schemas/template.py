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

class TemplateDeleteResponse(BaseSchema):
    result: str
    template_id: int