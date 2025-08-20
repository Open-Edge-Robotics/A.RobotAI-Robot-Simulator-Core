from dataclasses import dataclass
import os
import queue
import subprocess
import time
import yaml
import re
from threading import Event, Thread
from datetime import datetime, timedelta
from typing import Optional, List

from minio import S3Error

from pod_server.src.database import minio_conn

@dataclass
class RosbagProgress:
    """Rosbag 진행 상황 정보"""
    current_time: float = 0.0
    total_duration: float = 0.0
    messages_played: int = 0
    total_messages: int = 0
    progress_percentage: float = 0.0
    is_playing: bool = False
    current_topic: str = ""


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
        self.current_file_path = None  # 이제 디렉토리 경로를 저장
        self.total_elapsed_time = 0
        self.pause_event = Event()  # 일시정지 기능
        self.pause_event.set()  # 기본적으로 일시정지 해제 상태
        
        # 실시간 출력을 위한 추가 속성
        self.progress = RosbagProgress()
        self.output_queue = queue.Queue()
        self.output_thread = None
        
    def _read_output_stream(self, stream, stream_name):
        """실시간으로 stdout/stderr를 읽어서 큐에 저장"""
        try:
            for line in iter(stream.readline, b''):
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    self.output_queue.put((stream_name, decoded_line))
                    
                    # 진행률 정보 파싱
                    self._parse_progress_info(decoded_line)
                    
                if self.stop_event.is_set():
                    break
        except Exception as e:
            self.output_queue.put(("ERROR", f"Error reading {stream_name}: {str(e)}"))
        finally:
            stream.close()
            
    def _parse_progress_info(self, line: str):
        """ros2 bag play 출력에서 진행률 정보 파싱"""
        try:
            # ros2 bag play의 일반적인 출력 패턴들
            if "Playing back" in line:
                self.progress.is_playing = True
                print(f"🎬 {line}")
                
            elif "%" in line and "played" in line:
                # 진행률 정보가 있는 경우 파싱
                parts = line.split()
                for i, part in enumerate(parts):
                    if "%" in part:
                        try:
                            percentage = float(part.replace("%", ""))
                            self.progress.progress_percentage = percentage
                            print(f"📊 진행률: {percentage:.1f}%")
                        except ValueError:
                            pass
                            
            elif "messages" in line.lower():
                # 메시지 수 정보 파싱
                print(f"📨 {line}")
                
            elif line.startswith("[") or "rosbag" in line.lower():
                # 일반적인 rosbag 로그
                print(f"🎒 {line}")
                
        except Exception as e:
            # 파싱 에러는 무시하고 계속 진행
            pass

    def _print_realtime_output(self):
        """큐에서 출력을 실시간으로 처리"""
        while not self.stop_event.is_set() or not self.output_queue.empty():
            try:
                stream_name, line = self.output_queue.get(timeout=0.1)
                
                if stream_name == "STDOUT":
                    print(f"  📤 {line}")
                elif stream_name == "STDERR":
                    print(f"  ⚠️  {line}")
                elif stream_name == "ERROR":
                    print(f"  ❌ {line}")
                    
                self.output_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"  🔧 Output processing error: {str(e)}")

    def play_loop_with_realtime_output(self, bag_directory_path, max_loops=None, 
                                 delay_between_loops=0, execution_duration=None):
        """실시간 출력을 지원하는 ros2 bag play 반복 실행"""
        
        # None인 경우 기본값 설정
        if max_loops is None or max_loops <= 0:
            max_loops = 1
            print(f"Warning: max_loops was None or invalid, defaulting to 1")
        
        print(f"Debug Info:")
        print(f"   max_loops: {max_loops} (type: {type(max_loops)})")
        print(f"   delay_between_loops: {delay_between_loops}")
        print(f"   execution_duration: {execution_duration}")
        
        self.is_playing = True
        self.current_loop_count = 0
        self.max_loop_count = max_loops
        self.delay_between_loops = delay_between_loops
        self.execution_duration = execution_duration
        self.current_file_path = bag_directory_path
        self.start_time = datetime.now()
        self.total_elapsed_time = 0

        if execution_duration:
            self.end_time = self.start_time + timedelta(seconds=execution_duration)

        while not self.stop_event.is_set():
            self.pause_event.wait()
            
            print(f"Loop start - current_count: {self.current_loop_count}, max_loops: {max_loops}")

            if self.end_time and datetime.now() >= self.end_time:
                print(f"Execution time limit reached ({execution_duration}s)")
                break

            # 단순화된 조건 - max_loops는 이제 항상 유효한 숫자
            if self.current_loop_count >= max_loops:
                print(f"Maximum loop count reached ({max_loops})")
                print(f"   Current count: {self.current_loop_count}")
                break

            try:
                loop_start_time = datetime.now()

                # ros2 bag play 명령어 구성
                command = ['ros2', 'bag', 'play', str(bag_directory_path)]
                command.extend(['--clock'])

                print(f"\nStarting rosbag playback (loop {self.current_loop_count + 1}/{max_loops})")
                print(f"Command: {' '.join(command)}")
                print(f"Directory: {bag_directory_path}")

                # 실시간 출력을 위한 subprocess 실행
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=1,
                    universal_newlines=True,
                    text=True
                )

                # 출력 스트림 읽기 스레드 시작
                stdout_thread = Thread(
                    target=self._read_output_stream,
                    args=(process.stdout, "STDOUT")
                )
                stderr_thread = Thread(
                    target=self._read_output_stream,
                    args=(process.stderr, "STDERR")
                )
                
                # 출력 처리 스레드 시작
                if not self.output_thread or not self.output_thread.is_alive():
                    self.output_thread = Thread(target=self._print_realtime_output)
                    self.output_thread.start()

                stdout_thread.start()
                stderr_thread.start()

                # 프로세스 상태 모니터링
                while process.poll() is None:
                    if self.stop_event.is_set():
                        print("Terminating rosbag process...")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            print("Force killing rosbag process...")
                            process.kill()
                            process.wait()
                        break
                    time.sleep(0.1)

                # 스레드 정리
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)

                # 프로세스 종료 상태 확인
                if process.returncode != 0 and not self.stop_event.is_set():
                    print(f"Rosbag process failed with return code: {process.returncode}")
                    time.sleep(0.5)
                    break

                loop_end_time = datetime.now()
                loop_duration = (loop_end_time - loop_start_time).total_seconds()
                self.total_elapsed_time += loop_duration

                # 반드시 루프 카운트 증가
                self.current_loop_count += 1
                
                # 프로세스 결과 확인
                if process.returncode == 0:
                    print(f"Loop {self.current_loop_count} completed successfully in {loop_duration:.2f}s")
                else:
                    print(f"Loop {self.current_loop_count} failed with return code: {process.returncode}")
                    print(f"   Duration: {loop_duration:.2f}s")
                    
                # 최대 루프 수 체크 (루프 카운트 증가 후)
                if self.current_loop_count >= max_loops:
                    print(f"Reached maximum loops ({max_loops}), stopping")
                    break

                # 루프 간 지연 시간 적용
                if delay_between_loops > 0 and not self.stop_event.is_set():
                    print(f"Waiting {delay_between_loops}s before next loop...")
                    for _ in range(delay_between_loops * 10):
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

            except Exception as e:
                print(f"Exception during rosbag playback: {e}")
                # 예외 발생 시에도 루프 카운트 증가
                self.current_loop_count += 1
                
                # 최대 루프 수 체크
                if self.current_loop_count >= max_loops:
                    print(f"Reached maximum loops ({max_loops}) after exception, stopping")
                    break

        self.is_playing = False
        self.progress.is_playing = False
        print(f"\nRosbag playback stopped. Total loops: {self.current_loop_count}, Total time: {self.total_elapsed_time:.2f}s")

    def play_loop(self, bag_directory_path, max_loops=None, delay_between_loops=0,
                  execution_duration=None):
        """ros2 bag play 반복 실행 (디렉토리 기반으로 수정)"""
        self.is_playing = True
        self.current_loop_count = 0
        self.max_loop_count = max_loops
        self.delay_between_loops = delay_between_loops
        self.execution_duration = execution_duration
        self.current_file_path = bag_directory_path
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

                # ros2 bag play 명령어 구성 (디렉토리 경로 사용)
                command = ['ros2', 'bag', 'play', str(bag_directory_path)]

                # 추가 옵션들
                command.extend(['--clock'])  # 시뮬레이션 시간 사용

                print(f"Starting rosbag playback (loop {self.current_loop_count + 1}/{max_loops or '∞'})")
                print(f"Command: {' '.join(command)}")

                # subprocess 실행
                process = subprocess.Popen(command)

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
                          delay_between_loops: int = 0, execution_duration: Optional[int] = None,
                          enable_realtime_output: bool = True):
        """
        ros2 bag play 실행 (디렉토리 기반으로 수정)

        Args:
            object_path: S3 객체 경로 (디렉토리)
            max_loops: 최대 반복 횟수 (None이면 무한 반복)
            delay_between_loops: 루프 간 지연 시간 (초)
            execution_duration: 최대 실행 시간 (초)
            enable_realtime_output: 실시간 출력 활성화 여부
        """
        if self.is_playing:
            raise Exception("Another rosbag playback is already running. Stop it first.")

        # 디렉토리 전체 다운로드로 변경
        bag_directory_path = await self.download_bag_directory(object_path)

        # 디렉토리 존재 및 bag 구조 확인
        if not os.path.exists(bag_directory_path):
            raise FileNotFoundError(f"Bag directory not found: {bag_directory_path}")
        
        if not self.validate_bag_structure(bag_directory_path):
            raise ValueError(f"Invalid bag structure in directory: {bag_directory_path}")

        # 기존 실행 중단
        if self.play_thread and self.play_thread.is_alive():
            self.stop_event.set()
            self.play_thread.join(timeout=5)

        # 새로운 실행 시작
        self.stop_event.clear()
        self.pause_event.set()  # 일시정지 해제

        if enable_realtime_output:
            self.play_thread = Thread(
                target=self.play_loop_with_realtime_output,
                args=(bag_directory_path, max_loops, delay_between_loops, execution_duration)
            )
        else:
            # 기존 방식 (출력 없음)
            self.play_thread = Thread(
                target=self.play_loop,
                args=(bag_directory_path, max_loops, delay_between_loops, execution_duration)
            )
            
        self.play_thread.start()

        return {
            "status": "started",
            "bag_directory": bag_directory_path,
            "max_loops": max_loops,
            "delay_between_loops": delay_between_loops,
            "execution_duration": execution_duration,
            "realtime_output_enabled": enable_realtime_output,
            "start_time": self.start_time.isoformat() if self.start_time else None
        }

    def validate_bag_structure(self, bag_directory: str) -> bool:
        """
        ROS 2 bag 디렉토리 구조 검증
        
        Args:
            bag_directory: bag 디렉토리 경로
            
        Returns:
            bool: 유효한 구조인지 여부
        """
        if not os.path.isdir(bag_directory):
            print(f"Path is not a directory: {bag_directory}")
            return False
        
        # metadata.yaml 파일 확인
        metadata_file = os.path.join(bag_directory, "metadata.yaml")
        if not os.path.exists(metadata_file):
            print(f"metadata.yaml not found in {bag_directory}")
            return False
        
        # .db3 파일 존재 확인
        files = os.listdir(bag_directory)
        db3_files = [f for f in files if f.endswith('.db3')]
        
        if len(db3_files) == 0:
            print(f"No .db3 files found in {bag_directory}")
            return False
        
        print(f"Valid bag structure: metadata.yaml + {len(db3_files)} .db3 file(s)")
        return True
    
    def sanitize_filename(self, filename: str) -> str:
        """파일명에서 문제가 될 수 있는 문자들 정리"""
        return filename.replace(':', '_')

    def fix_metadata_yaml(self, bag_directory: str):
        """metadata.yaml의 파일명 참조도 수정"""
        metadata_path = os.path.join(bag_directory, "metadata.yaml")
        
        if not os.path.exists(metadata_path):
            return
        
        try:
            # metadata.yaml 읽기
            with open(metadata_path, 'r') as f:
                metadata = yaml.safe_load(f)
            
            # 파일 경로들 수정
            if 'rosbag2_bagfile_information' in metadata:
                if 'relative_file_paths' in metadata['rosbag2_bagfile_information']:
                    file_paths = metadata['rosbag2_bagfile_information']['relative_file_paths']
                    updated_paths = []
                    
                    for path in file_paths:
                        sanitized_path = self.sanitize_filename(path)
                        updated_paths.append(sanitized_path)
                        print(f"Updated path: {path} -> {sanitized_path}")
                    
                    metadata['rosbag2_bagfile_information']['relative_file_paths'] = updated_paths
            
            # metadata.yaml 다시 쓰기
            with open(metadata_path, 'w') as f:
                yaml.dump(metadata, f, default_flow_style=False)
            
            print(f"Updated metadata.yaml in {bag_directory}")
            
        except Exception as e:
            print(f"Error updating metadata.yaml: {e}")
            raise

    async def download_bag_directory(self, object_path: str) -> str:
        """
        S3에서 bag 디렉토리 전체 다운로드
        
        Args:
            object_path: S3 객체 경로 (디렉토리)
            
        Returns:
            str: 다운로드된 로컬 디렉토리 경로
        """
        # 디렉토리 이름 추출
        directory_name = os.path.basename(object_path.rstrip('/'))
        local_bag_dir = os.path.join("/rosbag-data/bagfiles", directory_name)
        
        # 로컬 디렉토리 생성
        os.makedirs(local_bag_dir, exist_ok=True)
        
        try:
            minio_client = minio_conn.client
            
            # S3에서 해당 prefix의 모든 객체 나열
            print(f"Listing objects with prefix: {object_path}")
            objects = minio_client.list_objects(
                bucket_name=minio_conn.bucket_name,
                prefix=object_path,
                recursive=True
            )
            
            downloaded_files = []
            for obj in objects:
                # 파일명 추출 (prefix 제거)
                relative_path = obj.object_name[len(object_path):].lstrip('/')
                if not relative_path:  # 디렉토리 자체인 경우 스킵
                    continue
                    
                local_file_path = os.path.join(local_bag_dir, relative_path)
                
                # 하위 디렉토리가 있는 경우 생성
                local_file_dir = os.path.dirname(local_file_path)
                if local_file_dir != local_bag_dir:
                    os.makedirs(local_file_dir, exist_ok=True)
                
                # 파일이 이미 존재하는지 확인
                if os.path.exists(local_file_path):
                    print(f"File already exists: {local_file_path}")
                    downloaded_files.append(relative_path)
                    continue
                
                print(f"Downloading {obj.object_name} to {local_file_path}")
                minio_client.fget_object(
                    bucket_name=minio_conn.bucket_name,
                    object_name=obj.object_name,
                    file_path=local_file_path
                )
                downloaded_files.append(relative_path)
            
            print(f"Downloaded {len(downloaded_files)} files to {local_bag_dir}")
            print(f"Files: {downloaded_files}")
            
            if not downloaded_files:
                raise ValueError(f"No files found for prefix: {object_path}")
                
        except S3Error as e:
            print(f"Error downloading bag directory: {e}")
            raise e
        except Exception as e:
            print(f"Unexpected error downloading bag directory: {e}")
            raise e
        
        self.fix_metadata_yaml(local_bag_dir)

        return local_bag_dir

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
        """현재 rosbag 상태 반환 (디렉토리 정보 포함)"""
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
                "current_bag_directory": self.current_file_path,  # 명칭 변경
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None
            }

        return {
            "status": "Stopped",
            "total_loops_completed": self.current_loop_count,
            "total_elapsed_time": self.total_elapsed_time,
            "last_bag_directory": self.current_file_path  # 명칭 변경
        }

    async def get_file_info(self, object_path: str):
        """rosbag 디렉토리 정보 조회 (구조 변경)"""
        try:
            bag_directory = await self.download_bag_directory(object_path)

            if not os.path.exists(bag_directory):
                return {"error": "Directory not found"}

            # 디렉토리 구조 검증
            if not self.validate_bag_structure(bag_directory):
                return {"error": "Invalid bag structure"}

            # 디렉토리 크기 계산
            total_size = 0
            file_list = []
            for root, dirs, files in os.walk(bag_directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    file_list.append({
                        "name": file,
                        "size_bytes": file_size,
                        "type": "metadata" if file.endswith('.yaml') else "data" if file.endswith('.db3') else "other"
                    })

            # ros2 bag info 명령어로 상세 정보 가져오기
            try:
                result = subprocess.run(['ros2', 'bag', 'info', bag_directory],
                                        capture_output=True, text=True, check=True)
                bag_info = result.stdout
            except subprocess.CalledProcessError as e:
                bag_info = f"Error getting bag info: {e}"

            return {
                "bag_directory": bag_directory,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "file_count": len(file_list),
                "files": file_list,
                "bag_info": bag_info
            }
        except Exception as e:
            return {"error": str(e)}

    # 하위 호환성을 위한 별칭 (deprecated)
    async def download_bag_file(self, object_path: str):
        """
        하위 호환성을 위한 메서드 (deprecated)
        새로운 download_bag_directory 사용 권장
        """
        print("Warning: download_bag_file is deprecated. Use download_bag_directory instead.")
        return await self.download_bag_directory(object_path)


# 기존 RosbagService와의 호환성을 위한 별칭
RosbagService = EnhancedRosbagService