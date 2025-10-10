class CustomRequestValidationError(Exception):
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
