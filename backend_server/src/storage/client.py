from abc import ABC, abstractmethod
from typing import List

class StorageClient(ABC):
    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str):
        """로컬 파일을 원격 스토리지에 업로드"""
        pass

    @abstractmethod
    def create_directory(self, dir_path: str):
        """원격 스토리지에 디렉토리 생성"""
        pass

    @abstractmethod
    def list_files(self, dir_path: str) -> List[str]:
        """
        디렉토리 내 파일 목록 반환
        :param dir_path: 원격 스토리지 내 디렉토리 경로
        :return: 파일명 리스트
        """
        pass
    
    @abstractmethod
    def get_presigned_url(self, object_name: str, expires: int = 3600) -> str:
        """원격 파일 다운로드용 presigned URL 반환"""
        pass
    
    @abstractmethod
    def delete_file(self, remote_path: str):
        """원격 스토리지에서 파일 삭제"""
        pass
    
    @abstractmethod
    def delete_directory(self, dir_path: str):
        """디렉토리 내 모든 파일 삭제 (MinIO는 prefix 기반)"""
        pass