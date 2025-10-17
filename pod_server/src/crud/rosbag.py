import os
import subprocess
import time
from threading import Event, Thread
from datetime import datetime, timedelta
from typing import Optional

from minio import S3Error

from pod_server.src.database import minio_conn


class EnhancedRosbagService:
    def __init__(self):
        self.stop_event = Event()  # 중단 요청 상태 관리
        self.play_thread = None  # 백그라운드 실행용 Thread
        self.is_playing = False  # 상태 확인용
        self.stop_thread = None  # 백그라운드 중단용

        # 고도화된 기능을 위한 추가 속성
        self.start_time = None
        self.end_time = None
        self.current_loop_count = 0
        self.max_loop_count = None
        self.delay_between_loops = 0
        self.execution_duration = None
        self.current_file_path = None
        self.total_elapsed_time = 0
        self.pause_event = Event()  # 일시정지 기능
        self.pause_event.set()  # 기본적으로 일시정지 해제 상태

    def play_loop(self, file_path, max_loops=None, delay_between_loops=0,
                  execution_duration=None):
        """ros2 bag play 반복 실행 (고도화된 버전)"""
        self.is_playing = True
        self.current_loop_count = 0
        self.max_loop_count = max_loops
        self.delay_between_loops = delay_between_loops
        self.execution_duration = execution_duration
        self.current_file_path = file_path
        self.start_time = datetime.now()
        self.total_elapsed_time = 0

        # 실행 시간 제한이 있는 경우 종료 시간 계산
        if execution_duration:
            self.end_time = self.start_time + timedelta(seconds=execution_duration)

        while not self.stop_event.is_set():
            # 일시정지 상태 확인
            self.pause_event.wait()

            # 실행 시간 제한 확인
            if self.end_time and datetime.now() >= self.end_time:
                print(f"Execution time limit reached ({execution_duration}s)")
                break

            # 반복 횟수 제한 확인
            if max_loops and self.current_loop_count >= max_loops:
                print(f"Maximum loop count reached ({max_loops})")
                break

            try:
                loop_start_time = datetime.now()

                # ros2 bag play 명령어 구성
                command = ['ros2', 'bag', 'play', str(file_path)]

                # 추가 옵션들
                command.extend(['--clock'])  # 시뮬레이션 시간 사용

                print(f"Starting rosbag playback (loop {self.current_loop_count + 1}/{max_loops or '∞'})")
                print(f"Command: {' '.join(command)}")

                # subprocess 실행
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # 프로세스가 완료될 때까지 대기하면서 중단 신호 확인
                while process.poll() is None:
                    if self.stop_event.is_set():
                        process.terminate()
                        process.wait()
                        break
                    time.sleep(0.1)

                if process.returncode != 0 and not self.stop_event.is_set():
                    stderr_output = process.stderr.read().decode()
                    print(f"Error during rosbag playback: {stderr_output}")
                    break

                loop_end_time = datetime.now()
                loop_duration = (loop_end_time - loop_start_time).total_seconds()
                self.total_elapsed_time += loop_duration

                self.current_loop_count += 1
                print(f"Loop {self.current_loop_count} completed in {loop_duration:.2f}s")

                # 루프 간 지연 시간 적용
                if delay_between_loops > 0 and not self.stop_event.is_set():
                    print(f"Waiting {delay_between_loops}s before next loop...")
                    for _ in range(delay_between_loops * 10):  # 0.1초 단위로 체크
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

            except Exception as e:
                print(f"Exception during rosbag playback: {e}")
                break

        self.is_playing = False
        print(
            f"Rosbag playback stopped. Total loops: {self.current_loop_count}, Total time: {self.total_elapsed_time:.2f}s")

    async def play_rosbag(self, object_path: str, max_loops: Optional[int] = None,
                          delay_between_loops: int = 0, execution_duration: Optional[int] = None
                          ):
        """
        ros2 bag play 실행 (고도화된 버전)

        Args:
            object_path: S3 객체 경로
            max_loops: 최대 반복 횟수 (None이면 무한 반복)
            delay_between_loops: 루프 간 지연 시간 (초)
            execution_duration: 최대 실행 시간 (초)
        """
        if self.is_playing:
            raise Exception("Another rosbag playback is already running. Stop it first.")

        file_path = await self.download_bag_file(object_path)

        # 파일 존재 확인
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Bag file not found: {file_path}")

        # 기존 실행 중단
        if self.play_thread and self.play_thread.is_alive():
            self.stop_event.set()
            self.play_thread.join(timeout=5)

        # 새로운 실행 시작
        self.stop_event.clear()
        self.pause_event.set()  # 일시정지 해제
        self.play_thread = Thread(
            target=self.play_loop,
            args=(file_path, max_loops, delay_between_loops, execution_duration)
        )
        self.play_thread.start()

        return {
            "status": "started",
            "file_path": file_path,
            "max_loops": max_loops,
            "delay_between_loops": delay_between_loops,
            "execution_duration": execution_duration,
            "start_time": self.start_time.isoformat() if self.start_time else None
        }

    async def pause_rosbag(self):
        """rosbag 일시정지"""
        if not self.is_playing:
            return {"status": "error", "message": "No rosbag is currently playing"}

        self.pause_event.clear()
        return {"status": "paused", "message": "Rosbag playback paused"}

    async def resume_rosbag(self):
        """rosbag 재개"""
        if not self.is_playing:
            return {"status": "error", "message": "No rosbag is currently playing"}

        self.pause_event.set()
        return {"status": "resumed", "message": "Rosbag playback resumed"}

    async def stop_rosbag(self):
        """ros2 bag play 중단"""
        if not self.is_playing and (not self.play_thread or not self.play_thread.is_alive()):
            return {"status": "already_stopped", "message": "No rosbag is currently playing"}

        if self.stop_thread and self.stop_thread.is_alive():
            return {"status": "stopping", "message": "Stop request is already being processed"}

        self.stop_thread = Thread(target=self.stop_rosbag_background)
        self.stop_thread.start()
        return {"status": "stopping", "message": "Stopping rosbag in background"}

    def stop_rosbag_background(self):
        """백그라운드에서 rosbag 중단 처리"""
        try:
            if self.play_thread and self.play_thread.is_alive():
                self.stop_event.set()
                self.pause_event.set()  # 일시정지 상태라면 해제하여 종료 가능하게 함

                # Thread 종료 대기 (최대 10초)
                self.play_thread.join(timeout=10)

                if self.play_thread.is_alive():
                    print("Warning: Play thread did not stop gracefully")

            self.is_playing = False
            print("Rosbag playback stopped successfully")
        except Exception as e:
            print(f"Error stopping rosbag: {e}")
            self.is_playing = False

    async def get_status(self):
        """현재 rosbag 상태 반환 (고도화된 버전)"""
        if self.is_playing:
            current_time = datetime.now()
            elapsed_time = (current_time - self.start_time).total_seconds() if self.start_time else 0
            remaining_time = None

            if self.end_time:
                remaining_time = (self.end_time - current_time).total_seconds()
                remaining_time = max(0, remaining_time)

            # 예상 총 소요 시간 계산
            estimated_total_time = None
            if self.max_loop_count and self.current_loop_count > 0:
                avg_loop_time = self.total_elapsed_time / self.current_loop_count
                estimated_total_time = avg_loop_time * self.max_loop_count + (
                            self.max_loop_count - 1) * self.delay_between_loops

            return {
                "status": "Running" if not self.pause_event.is_set() else "Paused" if self.pause_event.is_set() else "Running",
                "is_paused": not self.pause_event.is_set(),
                "current_loop": self.current_loop_count,
                "max_loops": self.max_loop_count,
                "elapsed_time": elapsed_time,
                "total_elapsed_time": self.total_elapsed_time,
                "remaining_time": remaining_time,
                "estimated_total_time": estimated_total_time,
                "delay_between_loops": self.delay_between_loops,
                "current_file": self.current_file_path,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None
            }

        return {
            "status": "Stopped",
            "total_loops_completed": self.current_loop_count,
            "total_elapsed_time": self.total_elapsed_time,
            "last_file": self.current_file_path
        }

    async def get_file_info(self, object_path: str):
        """rosbag 파일 정보 조회"""
        try:
            file_path = await self.download_bag_file(object_path)

            if not os.path.exists(file_path):
                return {"error": "File not found"}

            # 파일 크기
            file_size = os.path.getsize(file_path)

            # ros2 bag info 명령어로 상세 정보 가져오기
            try:
                result = subprocess.run(['ros2', 'bag', 'info', file_path],
                                        capture_output=True, text=True, check=True)
                bag_info = result.stdout
            except subprocess.CalledProcessError as e:
                bag_info = f"Error getting bag info: {e}"

            return {
                "file_path": file_path,
                "file_size_bytes": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "bag_info": bag_info
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    async def download_bag_file(object_path: str):
        """S3에서 bag 파일 다운로드"""
        file_name = os.path.basename(object_path)
        file_path = os.path.join("/rosbag-data/bagfiles", file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        try:
            minio_client = minio_conn.client

            # 파일이 이미 존재하는지 확인
            if os.path.exists(file_path):
                print(f"File already exists: {file_path}")
                return file_path

            print(f"Downloading bag file from {object_path} to {file_path}")
            minio_client.fget_object(
                bucket_name=minio_conn.bucket_name,
                object_name=object_path,
                file_path=file_path
            )
            print(f"Download completed: {file_path}")
        except S3Error as e:
            print(f"Error downloading bag file: {e}")
            raise e
        except Exception as e:
            print(f"Unexpected error downloading bag file: {e}")
            raise e

        return file_path


# 기존 RosbagService와의 호환성을 위한 별칭
#RosbagService = EnhancedRosbagService