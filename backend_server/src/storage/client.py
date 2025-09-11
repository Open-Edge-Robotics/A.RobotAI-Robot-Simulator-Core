from abc import ABC, abstractmethod

class StorageClient(ABC):
    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str):
        """로컬 파일을 원격 스토리지에 업로드"""
        pass

    @abstractmethod
    def create_directory(self, dir_path: str):
        """원격 스토리지에 디렉토리 생성"""
        pass
