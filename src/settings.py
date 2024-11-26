from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    API_STR: str
    DATABASE_URL: str
    MINIO_URL: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_NAME: str


settings = Settings()


class BaseSchema(BaseModel):
    class Config:
        alias_generator = to_camel
        populate_by_name = True

    def model_dump(self):
        return super().model_dump(by_alias=True)
