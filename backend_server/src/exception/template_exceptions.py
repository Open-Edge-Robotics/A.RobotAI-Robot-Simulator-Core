class TemplateError(Exception):
    """템플릿 관련 도메인 예외"""
    pass

class TemplateNotFoundError(TemplateError):
    def __init__(self, template_id: int):
        super().__init__(f"템플릿을 찾을 수 없습니다. ID: {template_id}")
