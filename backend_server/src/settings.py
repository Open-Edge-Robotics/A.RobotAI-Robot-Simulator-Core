from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    API_STR: str
    DATABASE_URL: str


settings = Settings()


class BaseSchema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def model_dump(self, **kwargs):
        return super().model_dump(by_alias=True)
