import os

from .client import StorageClient

class MinioStorageClient(StorageClient):
    def __init__(self, minio_client, bucket_name):
        self.client = minio_client
        self.bucket = bucket_name
        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)

    def upload_file(self, local_path: str, remote_path: str):
        self.client.fput_object(self.bucket, remote_path, local_path)

    def create_directory(self, dir_path: str):
        # MinIO는 실제 디렉토리가 없으므로 placeholder 파일 생성 방식으로 처리 가능
        placeholder_path = os.path.join("/tmp", ".placeholder")
        with open(placeholder_path, "w") as f:
            f.write("placeholder")
        self.upload_file(placeholder_path, os.path.join(dir_path, ".placeholder"))
        os.remove(placeholder_path)
