import asyncio
from dataclasses import dataclass
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import yaml
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
from typing import Optional
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
        # 로깅 설정
        self._setup_logging()
        
        self.stop_event = Event()
        self.play_thread = None
        self.is_playing = False
        self.stop_thread = None

        # 고도화된 기능을 위한 추가 속성
        self.start_time = None
        self.end_time = None
        self.current_loop_count = 0
        self.max_loop_count = None
        self.delay_between_loops = 0
        self.execution_duration = None
        self.current_file_path = None
        self.total_elapsed_time = 0
        self.pause_event = Event()
        self.pause_event.set()  # 기본적으로 일시정지 해제 상태
        
        # 실시간 출력을 위한 추가 속성
        self.progress = RosbagProgress()
        self.output_queue = queue.Queue()
        self.output_thread = None
        
        # 상태 구분을 위한 플래그
        self.is_stopped = False
        self.stop_reason = None # 정확한 종료 사유 ("completed", "user_stopped", "failed", None)
        
        self.logger.info("EnhancedRosbagService initialized")
        
    def _setup_logging(self):
        """로깅 설정"""
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s][%(levelname)s] %(message)s',
            datefmt='%H:%M:%S.%f',
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        # stdout 즉시 플러시 강제
        sys.stdout.reconfigure(line_buffering=True)
        
    def _get_env_config(self) -> dict:
        """환경변수에서 rosbag 재생 설정을 가져옵니다."""
        
        def safe_int_conversion(value: str, default: Optional[int] = None) -> Optional[int]:
            if not value or value.lower() in ['none', 'null', '']:
                return default
            try:
                return int(value)
            except ValueError:
                self.logger.warning(f"Invalid integer value '{value}', using default {default}")
                return default
        
        max_loops_env = os.getenv('REPEAT_COUNT')
        delay_between_loops_env = os.getenv('DELAY_BETWEEN_LOOPS', '0')
        execution_duration_env = os.getenv('EXECUTION_TIME')
        
        config = {
            'max_loops': safe_int_conversion(max_loops_env, 1),
            'delay_between_loops': safe_int_conversion(delay_between_loops_env, 0),
            'execution_duration': safe_int_conversion(execution_duration_env, None)
        }
        
        self.logger.info(f"Environment config loaded: {config}")
        return config

    def play_loop_with_realtime_output(self, bag_directory_path, max_loops=None, 
                                     delay_between_loops=0, execution_duration=None):
        """실시간 출력 + 로그 포맷 적용, 반복 실행"""
        
        try:
            self.logger.info("===== METHOD START ===== play_loop_with_realtime_output called")
            self.logger.info(f"Thread ID: {threading.current_thread().ident}")
            self.logger.info(f"Current time: {datetime.now(timezone.utc)}")
            
            if max_loops is None or max_loops <= 0:
                max_loops = 1
                self.logger.warning("max_loops was None or invalid, defaulting to 1")

            self.logger.info("Debug Info:")
            self.logger.info(f"   max_loops: {max_loops} (type: {type(max_loops)})")
            self.logger.info(f"   delay_between_loops: {delay_between_loops}")
            self.logger.info(f"   execution_duration: {execution_duration}")
            
            # 이벤트 상태 확인
            self.logger.info("Initial event states:")
            self.logger.info(f"   stop_event.is_set(): {self.stop_event.is_set()}")
            self.logger.info(f"   pause_event.is_set(): {getattr(self.pause_event, '_flag', 'N/A')}")
            
            # 이벤트 초기화
            self.logger.info("Initializing events...")
            self.stop_event.clear()
            self.pause_event.set()
            self.logger.info(f"Events initialized - stop_event: {self.stop_event.is_set()}, pause_event set")

            # 상태 변수 설정
            self.is_playing = True
            self.is_stopped = False
            self.stop_reason = None  # 재생 시작 시 초기화
            self.current_loop_count = 0
            self.max_loop_count = max_loops
            self.delay_between_loops = delay_between_loops
            self.execution_duration = execution_duration
            self.current_file_path = bag_directory_path
            self.start_time = datetime.now(timezone.utc)
            self.total_elapsed_time = 0

            if execution_duration:
                self.end_time = self.start_time + timedelta(seconds=execution_duration)
                self.logger.info(f"Execution will end at: {self.end_time}")

            self.logger.info("===== ENTERING MAIN LOOP =====")
            
            while not self.stop_event.is_set():
                self.logger.debug(f"=== Starting loop iteration - current_loop_count: {self.current_loop_count}, max_loops: {max_loops} ===")
                
                if self.current_loop_count >= max_loops:
                    self.logger.info(f"Maximum loop count reached ({max_loops}), current count: {self.current_loop_count}")
                    break

                self.logger.debug("Checking pause_event...")
                self.pause_event.wait()
                self.logger.debug("pause_event cleared, continuing...")

                if self.end_time and datetime.now(timezone.utc) >= self.end_time:
                    self.logger.info(f"Execution time limit reached ({execution_duration}s)")
                    break

                try:
                    loop_start_time = datetime.now(timezone.utc)
                    command = ['ros2', 'bag', 'play', str(bag_directory_path), '--clock']

                    self.logger.info(f"Starting rosbag playback (loop {self.current_loop_count + 1}/{max_loops})")
                    self.logger.info(f"Command: {' '.join(command)}")
                    self.logger.info(f"Directory: {bag_directory_path}")

                    self.logger.debug("About to create subprocess...")
                    command_str = f"source /opt/ros/humble/setup.bash && {' '.join(command)}"
                    
                    # unbuffered subprocess 실행
                    process = subprocess.Popen(
                        command_str, 
                        shell=True, 
                        executable='/bin/bash',
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE,
                        bufsize=0,
                        universal_newlines=False
                    )
                    
                    self.logger.info(f"Subprocess created successfully, PID: {process.pid}")

                    # 실시간 출력 처리
                    def realtime_output_reader(stream, stream_type):
                        try:
                            while True:
                                line = stream.readline()
                                if not line:
                                    break
                                
                                line_str = line.decode('utf-8', errors='ignore').strip()
                                if line_str:
                                    timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]
                                    output = f"[{timestamp}][{stream_type}] {line_str}"
                                    print(output, flush=True)
                                    
                        except Exception as e:
                            error_msg = f"Error reading {stream_type}: {e}"
                            self.logger.error(error_msg)
                    
                    self.logger.debug("Creating and starting IO threads...")
                    stdout_thread = threading.Thread(
                        target=realtime_output_reader, 
                        args=(process.stdout, "STDOUT")
                    )
                    stderr_thread = threading.Thread(
                        target=realtime_output_reader, 
                        args=(process.stderr, "STDERR")
                    )
                    
                    stdout_thread.start()
                    stderr_thread.start()

                    # 프로세스 모니터링
                    self.logger.debug(f"Monitoring process {process.pid}...")
                    while process.poll() is None:
                        if self.stop_event.is_set():
                            self.logger.info(f"Stop event detected, terminating process {process.pid}...")
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                self.logger.warning(f"Force killing rosbag process PID {process.pid}...")
                                process.kill()
                                process.wait()
                            break
                        time.sleep(0.1)

                    self.logger.info(f"Process {process.pid} finished with return code: {process.returncode}")
                    
                    # 스레드 정리
                    stdout_thread.join(timeout=2)
                    stderr_thread.join(timeout=2)

                    # 루프 통계 업데이트
                    loop_end_time = datetime.now(timezone.utc)
                    loop_duration = (loop_end_time - loop_start_time).total_seconds()
                    self.total_elapsed_time += loop_duration
                    self.current_loop_count += 1

                    if process.returncode == 0:
                        self.logger.info(f"Loop {self.current_loop_count}/{max_loops} completed successfully in {loop_duration:.2f}s")
                    else:
                        self.logger.error(f"Loop {self.current_loop_count}/{max_loops} failed with return code: {process.returncode}")

                    if self.current_loop_count >= max_loops:
                        self.logger.info(f"Reached maximum loops ({max_loops}), stopping")
                        break
                        
                    # 지연 처리
                    if delay_between_loops > 0 and not self.stop_event.is_set():
                        self.logger.info(f"Waiting {delay_between_loops}s before next loop...")
                        for i in range(delay_between_loops * 10):
                            if self.stop_event.is_set():
                                self.logger.debug("Stop event detected during delay, breaking...")
                                break
                            time.sleep(0.1)
                        self.logger.debug("Delay completed, starting next loop...")

                except Exception as e:
                    self.logger.error(f"Exception during rosbag playback: {e}")
                    self.logger.error(f"Exception type: {type(e).__name__}")
                    import traceback
                    self.logger.error(f"Traceback: {traceback.format_exc()}")
                    
                    self.current_loop_count += 1
                    self.logger.info(f"Exception occurred in loop {self.current_loop_count}/{max_loops}, continuing...")
                    
                    if self.current_loop_count >= max_loops:
                        self.logger.info(f"Reached maximum loops ({max_loops}) after exception, stopping")
                        break

            self.logger.info("===== EXITED MAIN LOOP =====")
            self.logger.debug(f"[DEBUG] Current loop count: {self.current_loop_count}, Max loops: {max_loops}")
            self.logger.debug(f"[DEBUG] Stop event set: {self.stop_event.is_set()}")
            self.logger.debug(f"[DEBUG] End time: {self.end_time}, Current time: {datetime.now(timezone.utc)}")
            self.logger.debug(f"[DEBUG] Is playing before exit: {self.is_playing}")
            self.logger.debug(f"[DEBUG] Total elapsed time so far: {self.total_elapsed_time:.2f}s")

            # 종료 사유 판단
            if self.stop_event.is_set():
                # 사용자 중지 요청
                self.stop_reason = "user_stopped"
                self.is_stopped = True
                self.logger.info("Rosbag stopped by user request (stop_event triggered)")
            elif self.current_loop_count >= max_loops:
                # 정상 완료 (최대 루프 수 도달)
                self.stop_reason = "completed"
                self.is_stopped = True
                self.logger.info(f"Rosbag completed normally (reached max loops: {max_loops})")
            elif self.end_time and datetime.now(timezone.utc) >= self.end_time:
                # 정상 완료 (시간 제한 도달)
                self.stop_reason = "completed"
                self.is_stopped = True
                self.logger.info(f"Rosbag completed normally (time limit reached: end_time={self.end_time})")
            else:
                # 기타 정상 완료
                self.stop_reason = "completed"
                self.is_stopped = True
                self.logger.info("Rosbag completed normally (other reasons)")

            # 상태 업데이트
            self.is_playing = False
            if hasattr(self, 'progress'):
                self.progress.is_playing = False

            # 최종 상태 로그
            self.logger.info(f"===== METHOD END ===== Rosbag playback stopped.")
            self.logger.info(f"Stop reason: {self.stop_reason}")
            self.logger.info(f"Is stopped: {self.is_stopped}")
            self.logger.info(f"Total loops executed: {self.current_loop_count}")
            self.logger.info(f"Total elapsed time: {self.total_elapsed_time:.2f}s")

            # 추가 디버깅: 루프별 상태 확인
            for i, loop_time in enumerate(getattr(self, "loop_times", []), 1):
                self.logger.debug(f"[DEBUG] Loop {i}: duration {loop_time:.2f}s")

        except Exception as global_e:
            self.logger.critical(f"Global exception in play_loop_with_realtime_output: {global_e}")
            self.logger.critical(f"Exception type: {type(global_e).__name__}")
            import traceback
            self.logger.critical(f"Traceback: {traceback.format_exc()}")
    
            # 예외로 인한 실패
            self.is_playing = False
            self.is_stopped = False  # failed 상태
            self.stop_reason = "failed"  # 실패 사유
            if hasattr(self, 'progress'):
                self.progress.is_playing = False
            self.logger.error(f"Rosbag failed due to exception: {self.stop_reason}")
        
        finally:
            self.logger.info("===== FINALLY BLOCK ===== Ensuring cleanup...")
            self.is_playing = False
            if hasattr(self, 'progress'):
                self.progress.is_playing = False
            self.logger.info("Cleanup completed.")
    
    def play_loop(self, bag_directory_path, max_loops=None, delay_between_loops=0, execution_duration=None):
        """ros2 bag play 반복 실행"""
        self.is_playing = True
        self.current_loop_count = 0
        self.max_loop_count = max_loops
        self.delay_between_loops = delay_between_loops
        self.execution_duration = execution_duration
        self.current_file_path = bag_directory_path
        self.start_time = datetime.now(timezone.utc)
        self.total_elapsed_time = 0

        if execution_duration:
            self.end_time = self.start_time + timedelta(seconds=execution_duration)

        while not self.stop_event.is_set():
            self.pause_event.wait()

            # 실행 시간 제한 확인
            if self.end_time and datetime.now(timezone.utc) >= self.end_time:
                self.logger.info(f"Execution time limit reached ({execution_duration}s)")
                break

            # 반복 횟수 제한 확인
            if max_loops and self.current_loop_count >= max_loops:
                self.logger.info(f"Maximum loop count reached ({max_loops})")
                break

            try:
                loop_start_time = datetime.now(timezone.utc)
                command = ['ros2', 'bag', 'play', str(bag_directory_path), '--clock']

                self.logger.info(f"Starting rosbag playback (loop {self.current_loop_count + 1}/{max_loops or '∞'})")
                self.logger.info(f"Command: {' '.join(command)}")

                command_str = f"source /opt/ros/humble/setup.bash && {' '.join(command)}"
                process = subprocess.Popen(
                    command_str, 
                    shell=True, 
                    executable='/bin/bash',
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE
                )

                # 프로세스 모니터링
                while process.poll() is None:
                    if self.stop_event.is_set():
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        break
                    time.sleep(0.1)

                if process.returncode != 0 and not self.stop_event.is_set():
                    stderr_output = process.stderr.read().decode()
                    self.logger.error(f"Error during rosbag playback: {stderr_output}")
                    break

                loop_end_time = datetime.now(timezone.utc)
                loop_duration = (loop_end_time - loop_start_time).total_seconds()
                self.total_elapsed_time += loop_duration
                self.current_loop_count += 1
                
                self.logger.info(f"Loop {self.current_loop_count} completed in {loop_duration:.2f}s")

                # 루프 간 지연 시간 적용
                if delay_between_loops > 0 and not self.stop_event.is_set():
                    self.logger.info(f"Waiting {delay_between_loops}s before next loop...")
                    for _ in range(delay_between_loops * 10):
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Exception during rosbag playback: {e}")
                break

        self.is_playing = False
        self.logger.info(f"Rosbag playback completed. Total loops: {self.current_loop_count}, Total time: {self.total_elapsed_time:.2f}s")

    async def play_rosbag(self, object_path: str):
        """ros2 bag play 실행"""
        if self.is_playing:
            raise Exception("Another rosbag playback is already running. Stop it first.")
        
        # 환경변수에서 설정값 가져오기
        config = self._get_env_config()
        max_loops = config['max_loops']
        delay_between_loops = config['delay_between_loops']
        execution_duration = config['execution_duration']
        
        # 로컬 디렉토리 경로 미리 계산
        directory_name = os.path.basename(object_path.rstrip('/'))
        bag_directory_path = os.path.join("/rosbag-data/bagfiles", directory_name)
        
        # 로컬에 이미 존재하고 유효한 구조인지 확인
        should_download = True
        if os.path.exists(bag_directory_path):
            if self.validate_bag_structure(bag_directory_path):
                self.logger.info(f"Using existing bag directory: {bag_directory_path}")
                should_download = False
            else:
                self.logger.warning(f"Invalid bag structure found, re-downloading: {bag_directory_path}")

        # 필요한 경우에만 다운로드
        if should_download:
            bag_directory_path = await self.download_bag_directory(object_path)

        if not os.path.exists(bag_directory_path):
            raise FileNotFoundError(f"Bag directory not found: {bag_directory_path}")
        
        if not self.validate_bag_structure(bag_directory_path):
            raise ValueError(f"Invalid bag structure in directory: {bag_directory_path}")

        # 기존 실행 중단
        if self.play_thread and self.play_thread.is_alive():
            self.logger.warning("기존 play_thread 위에서 실행 중인 rosbag 이 존재함")
            self.stop_event.set()
            self.play_thread.join(timeout=5)

        # 새로운 실행 시작
        self.stop_event.clear()
        self.pause_event.set()
        self.is_stopped = False # 새로운 재생 시작 시 초기화
        self.stop_reason = None

        self.play_thread = Thread(
            target=self.play_loop_with_realtime_output,
            args=(bag_directory_path, max_loops, delay_between_loops, execution_duration)
        )
        self.play_thread.start()
        
        time.sleep(0.1)  # 스레드 시작 시간 대기
        if not self.play_thread.is_alive():
            raise Exception("Failed to start rosbag thread")

        return {
            "status": "started",
            "message": "Rosbag playback started successfully"
        }

    def validate_bag_structure(self, bag_directory: str) -> bool:
        """ROS 2 bag 디렉토리 구조 검증"""
        if not os.path.isdir(bag_directory):
            self.logger.debug(f"Path is not a directory: {bag_directory}")
            return False
        
        # metadata.yaml 파일 확인
        metadata_file = os.path.join(bag_directory, "metadata.yaml")
        if not os.path.exists(metadata_file):
            self.logger.debug(f"metadata.yaml not found in {bag_directory}")
            return False
        
        # .db3 파일 존재 확인
        files = os.listdir(bag_directory)
        db3_files = [f for f in files if f.endswith('.db3')]
        
        if len(db3_files) == 0:
            self.logger.debug(f"No .db3 files found in {bag_directory}")
            return False
        
        self.logger.info(f"Valid bag structure: metadata.yaml + {len(db3_files)} .db3 file(s)")
        return True
    
    def sanitize_filename(self, filename: str) -> str:
        """파일명에서 문제가 될 수 있는 문자들 정리"""
        return filename.replace(':', '_')

    def fix_metadata_yaml(self, bag_directory: str):
        """metadata.yaml의 파일명 참조 수정"""
        metadata_path = os.path.join(bag_directory, "metadata.yaml")
        
        if not os.path.exists(metadata_path):
            return
        
        try:
            with open(metadata_path, 'r') as f:
                metadata = yaml.safe_load(f)
            
            if 'rosbag2_bagfile_information' in metadata:
                if 'relative_file_paths' in metadata['rosbag2_bagfile_information']:
                    file_paths = metadata['rosbag2_bagfile_information']['relative_file_paths']
                    updated_paths = [self.sanitize_filename(path) for path in file_paths]
                    metadata['rosbag2_bagfile_information']['relative_file_paths'] = updated_paths
                    self.logger.debug(f"Updated {len(updated_paths)} file paths in metadata.yaml")
            
            with open(metadata_path, 'w') as f:
                yaml.dump(metadata, f, default_flow_style=False)
            
            self.logger.info(f"Updated metadata.yaml in {bag_directory}")
                
        except Exception as e:
            self.logger.error(f"Error updating metadata.yaml: {e}")
            raise

    async def download_bag_directory(self, object_path: str) -> str:
        """S3에서 bag 디렉토리 전체 다운로드"""
        directory_name = os.path.basename(object_path.rstrip('/'))
        local_bag_dir = os.path.join("/rosbag-data/bagfiles", directory_name)
        
        os.makedirs(local_bag_dir, exist_ok=True)
        
        try:
            minio_client = minio_conn.client
            
            self.logger.info(f"Listing objects with prefix: {object_path}")
            objects = minio_client.list_objects(
                bucket_name=minio_conn.bucket_name,
                prefix=object_path,
                recursive=True
            )
            
            downloaded_files = []
            for obj in objects:
                relative_path = obj.object_name[len(object_path):].lstrip('/')
                if not relative_path:
                    continue
                    
                local_file_path = os.path.join(local_bag_dir, relative_path)
                
                local_file_dir = os.path.dirname(local_file_path)
                if local_file_dir != local_bag_dir:
                    os.makedirs(local_file_dir, exist_ok=True)
                
                if os.path.exists(local_file_path):
                    self.logger.debug(f"File already exists: {local_file_path}")
                    downloaded_files.append(relative_path)
                    continue
                
                self.logger.info(f"Downloading {obj.object_name} to {local_file_path}")
                minio_client.fget_object(
                    bucket_name=minio_conn.bucket_name,
                    object_name=obj.object_name,
                    file_path=local_file_path
                )
                downloaded_files.append(relative_path)
            
            self.logger.info(f"Downloaded {len(downloaded_files)} files to {local_bag_dir}")
            self.logger.debug(f"Files: {downloaded_files}")
            
            if not downloaded_files:
                raise ValueError(f"No files found for prefix: {object_path}")
                
        except Exception as e:
            self.logger.error(f"Error downloading bag directory: {e}")
            raise e
        
        self.fix_metadata_yaml(local_bag_dir)
        return local_bag_dir

    async def pause_rosbag(self):
        """rosbag 일시정지"""
        if not self.is_playing:
            return {"status": "error", "message": "No rosbag is currently playing"}

        self.pause_event.clear()
        self.logger.info("Rosbag playback paused")
        return {"status": "paused", "message": "Rosbag playback paused"}

    async def resume_rosbag(self):
        """rosbag 재개"""
        if not self.is_playing:
            return {"status": "error", "message": "No rosbag is currently playing"}

        self.pause_event.set()
        self.logger.info("Rosbag playback resumed")
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
                self.pause_event.set()
                self.play_thread.join(timeout=10)

                if self.play_thread.is_alive():
                    self.logger.warning("Play thread did not stop gracefully")

            self.is_playing = False
            self.is_stopped = True
            # stop_reason은 play_loop_with_realtime_output에서 "user_stopped"로 설정됨
            self.logger.info("Rosbag playback stopped successfully")
        except Exception as e:
            self.logger.error(f"Error stopping rosbag: {e}")
            self.is_playing = False
            self.is_stopped = False # 중단 처리 중 실패
            self.stop_reason = "failed"
    
    async def get_status_v2(self):
        """
        현재 rosbag 상태를 반환 - 4가지 상태 구분 가능
        1) 재생 중: isPlaying=True, stopReason=None
        2) 정상 완료: isPlaying=False, stopReason="completed"
        3) 사용자 중지: isPlaying=False, stopReason="user_stopped"
        4) 실패: isPlaying=False, stopReason="failed"
        """
        try:
            if self.is_playing:
                is_playing = True
                current_loop = self.current_loop_count
                max_loops = self.max_loop_count or 0
                is_stopped = False
                stop_reason = None  # 재생 중일 때는 None
            else:
                is_playing = False
                current_loop = self.current_loop_count if hasattr(self, "current_loop_count") else 0
                max_loops = self.max_loop_count if hasattr(self, "max_loop_count") else 0
                is_stopped = getattr(self, 'is_stopped', False)
                stop_reason = getattr(self, 'stop_reason', None)

            return {
                "isPlaying": is_playing,
                "isStopped": is_stopped,  # 기존 호환성 유지 (stopped vs failed 구분)
                "stopReason": stop_reason,  # 추가: 정확한 종료 사유
                "current_loop": current_loop,
                "max_loops": max_loops
            }

        except Exception as e:
            # 상태 조회 실패 시
            return {
                "isPlaying": False,
                "isStopped": False,
                "stopReason": "failed",  # 상태 조회 실패도 failed로 간주
                "current_loop": 0,
                "max_loops": 0,
                "error": str(e)
            }


    async def get_status(self):
        """현재 rosbag 상태 반환"""
        if self.is_playing:
            current_time = datetime.now(timezone.utc)
            elapsed_time = (current_time - self.start_time).total_seconds() if self.start_time else 0
            remaining_time = None

            if self.end_time:
                remaining_time = (self.end_time - current_time).total_seconds()
                remaining_time = max(0, remaining_time)

            estimated_total_time = None
            if self.max_loop_count and self.current_loop_count > 0:
                avg_loop_time = self.total_elapsed_time / self.current_loop_count
                estimated_total_time = avg_loop_time * self.max_loop_count + (
                    self.max_loop_count - 1) * self.delay_between_loops

            return {
                "status": "Running" if self.pause_event.is_set() else "Paused",
                "is_paused": not self.pause_event.is_set(),
                "current_loop": self.current_loop_count,
                "max_loops": self.max_loop_count,
                "elapsed_time": elapsed_time,
                "total_elapsed_time": self.total_elapsed_time,
                "remaining_time": remaining_time,
                "estimated_total_time": estimated_total_time,
                "delay_between_loops": self.delay_between_loops,
                "current_bag_directory": self.current_file_path,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None
            }

        return {
            "status": "Stopped",
            "total_loops_completed": self.current_loop_count,
            "total_elapsed_time": self.total_elapsed_time,
            "last_bag_directory": self.current_file_path
        }

    async def get_file_info(self, object_path: str):
        """rosbag 디렉토리 정보 조회"""
        try:
            bag_directory = await self.download_bag_directory(object_path)

            if not os.path.exists(bag_directory):
                return {"error": "Directory not found"}

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
                self.logger.error(f"Failed to get bag info: {e}")

            return {
                "bag_directory": bag_directory,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "file_count": len(file_list),
                "files": file_list,
                "bag_info": bag_info
            }
        except Exception as e:
            self.logger.error(f"Error getting file info: {e}")
            return {"error": str(e)}


# 기존 RosbagService와의 호환성을 위한 별칭
RosbagService = EnhancedRosbagService