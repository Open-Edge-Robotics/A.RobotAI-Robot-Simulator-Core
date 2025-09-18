import os

class RosbagFileValidator:
    @staticmethod
    def validate_files(file_paths: list[str]):
        """
        rosbag2 파일 검증
        - 업데이트 시: 1개 또는 2개 파일 허용
        - 새 업로드 시: 2개 파일 필요
        """
        if not file_paths:
            raise ValueError("업로드할 파일이 없습니다.")

        yaml_file, db3_file = None, None
        for f in file_paths:
            if f.endswith((".yaml", ".yml")):
                if yaml_file:
                    raise ValueError(f"중복된 YAML 파일 발견: {f}")
                yaml_file = f
            elif f.endswith(".db3"):
                if db3_file:
                    raise ValueError(f"중복된 DB3 파일 발견: {f}")
                db3_file = f
            else:
                raise ValueError(f"허용되지 않은 파일 형식입니다: {f}")

        if len(file_paths) == 2:
            # 새 업로드 시 반드시 2개 필요
            if not (yaml_file and db3_file):
                raise ValueError("rosbag2 업로드에는 .yaml/.yml 파일과 .db3 파일이 모두 필요합니다.")
