import os

class RosbagFileValidator:
    REQUIRED_FILES = ["metadata.yaml", ".db3"]

    @staticmethod
    def validate_files(file_paths: list):
        has_metadata = any(f.endswith("metadata.yaml") for f in file_paths)
        has_db3 = any(f.endswith(".db3") for f in file_paths)

        if not has_metadata or not has_db3:
            raise ValueError("rosbag2 업로드에는 metadata.yaml과 .db3 파일이 모두 필요합니다.")

        # 확장자 추가 검증 가능
        for f in file_paths:
            if not (f.endswith(".yaml") or f.endswith(".db3")):
                raise ValueError(f"허용되지 않은 파일 형식입니다: {f}")
