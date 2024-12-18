import os
import subprocess
from threading import Event, Thread

from minio import S3Error

from pod_server.src.database import minio_conn


class RosbagService:
    def __init__(self):
        self.stop_event = Event()  # 중단 요청 상태 관리
        self.play_thread = None  # 백그라운드 실행용 Thread
        self.is_playing = False  # 상태 확인용
        self.stop_thread = None  # 백그라운드 중단용

    def play_loop(self, file_path):
        """ros2 bag play 반복 실행"""
        self.is_playing = True

        while not self.stop_event.is_set():
            try:
                command = ['ros2', 'bag', 'play', str(file_path)]
                subprocess.run(command, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error while playing rosbag: {e}")
                break
        self.is_playing = False

    async def play_rosbag(self, object_path: str):
        file_path = await self.download_bag_file(object_path)

        # 기존 실행 중단
        if self.play_thread and self.play_thread.is_alive():
            self.stop_event.set()

        self.play_thread.join()

        # 새로운 실행 시작
        self.stop_event.clear()
        self.play_thread = Thread(target=self.play_loop, args=(file_path,))
        self.play_thread.start()

        return file_path

    async def stop_rosbag(self):
        """ros2 bag play 중단"""
        if self.stop_thread and self.stop_thread.is_alive():
            return {"message": "Stop request is already being processed"}

        self.stop_thread = Thread(target=self.stop_rosbag_background)
        self.stop_thread.start()
        return {"message": "Stopping rosbag in background"}

    def stop_rosbag_background(self):
        if self.play_thread and self.play_thread.is_alive():
            self.stop_event.set()
            self.play_thread.join()
        self.is_playing = False

    async def get_status(self):
        """현재 rosbag 상태 반환"""
        if self.is_playing:
            return {"status": "Running"}
        return {"status": "Stopped"}

    @staticmethod
    async def download_bag_file(object_path: str):
        file_name = os.path.basename(object_path)
        file_path = os.path.join("/rosbag-data/bagfiles", file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        try:
            minio_client = minio_conn.client
            minio_client.fget_object(
                bucket_name=minio_conn.bucket_name,
                object_name=object_path,
                file_path=file_path
            )
        except S3Error as e:
            print(f"Error downloading bag file: {e}")
        return file_path
