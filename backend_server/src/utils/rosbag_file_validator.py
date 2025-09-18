import os

class RosbagFileValidator:
    @staticmethod
    def validate_files(file_paths: list[str]):
        if len(file_paths) != 2:
            raise ValueError("rosbag2 업로드에는 정확히 2개의 파일(.yaml/.yml, .db3)이 필요합니다.")

        yaml_file, db3_file = None, None
        for f in file_paths:
            if f.endswith((".yaml", ".yml")):
                yaml_file = f
            elif f.endswith(".db3"):
                db3_file = f
            else:
                raise ValueError(f"허용되지 않은 파일 형식입니다: {f}")

        if not yaml_file or not db3_file:
            raise ValueError("rosbag2 업로드에는 .yaml/.yml 파일과 .db3 파일이 모두 필요합니다.")
