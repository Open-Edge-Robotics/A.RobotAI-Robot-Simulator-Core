import os
from .client import StorageClient
from minio import Minio
from settings import settings

class MinioStorageClient(StorageClient):
    _instance = None  # 싱글톤 인스턴스
    
    def __init__(self, minio_client: Minio, bucket_name: str, external_host: str):
        if MinioStorageClient._instance is not None:
            raise Exception("This class is a singleton! Use get_instance()")
        
        self.client = minio_client
        self.bucket = bucket_name
        self.external_host = external_host  # NodePort 외부 호스트(IP:PORT)

        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)

        MinioStorageClient._instance = self
        
    @classmethod
    def get_instance(cls, minio_client: Minio = None, bucket_name: str = None, external_host: str = None):
        if cls._instance is None:
            if minio_client is None or bucket_name is None:
                raise ValueError("minio_client and bucket_name must be provided for first initialization")
            cls(minio_client, bucket_name, external_host)
        return cls._instance

    def upload_file(self, local_path: str, remote_path: str):
        self.client.fput_object(self.bucket, remote_path, local_path)

    def create_directory(self, dir_path: str):
        # MinIO는 실제 디렉토리가 없으므로 placeholder 파일 생성 방식으로 처리 가능
        placeholder_path = os.path.join("/tmp", ".placeholder")
        with open(placeholder_path, "w") as f:
            f.write("placeholder")
        self.upload_file(placeholder_path, os.path.join(dir_path, ".placeholder"))
        os.remove(placeholder_path)
        
    def list_files(self, dir_path: str) -> list[str]:
        """
        디렉토리 내 파일 목록 반환 (placeholder 제외)
        :param dir_path: 디렉토리 경로 (MinIO 버킷 내 prefix)
        :return: 파일명 리스트
        """
        files = []
        objects = self.client.list_objects(self.bucket, prefix=f"{dir_path}/", recursive=True)
        
        for obj in objects:
            file_name = obj.object_name.split("/")[-1]
            if file_name != ".placeholder":  # placeholder 파일 제외
                files.append(file_name)
        
        return files

    def get_presigned_url(self, object_name: str) -> str:
        """
        파일 다운로드용 URL 생성 (Public 버킷용)
        :param object_name: 버킷 내 파일 경로
        :return: 직접 접근 URL
        """
        # Public 버킷이므로 직접 URL 생성
        if self.external_host:
            base_url = f"http://{self.external_host}"
        else:
            # MinIO 클라이언트의 endpoint 정보 사용
            endpoint = self.client._base_url._url.netloc
            scheme = "https" if self.client._base_url._url.scheme == "https" else "http"
            base_url = f"{scheme}://{endpoint}"
        
        url = f"{base_url}/{self.bucket}/{object_name}"
        print(f"Generated direct URL: {url}")
        return url