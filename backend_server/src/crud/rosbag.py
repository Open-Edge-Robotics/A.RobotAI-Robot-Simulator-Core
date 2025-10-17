from utils import debug_print
import httpx
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

class RosService:
    @staticmethod
    async def get_pod_status(pod_ip: str) -> dict:
        """
        비동기 방식으로 Pod 상태 조회 
        """
        logger.debug(f"Pod 상태 조회 시작: {pod_ip}")
        
        if not pod_ip:
            logger.warning("Pod IP가 비어있음 - 기본 상태 반환")
            return {
                "is_playing": False,
                "is_stopped": False,
                "stop_reason": "failed",
                "current_loop": 0,
                "max_loops": 0,
                "error": "Not Ready"
            }

        pod_api_url = f"http://{pod_ip}:8002/rosbag/status"
        logger.debug(f"Pod API 요청 URL: {pod_api_url}")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(pod_api_url)
                response.raise_for_status()
                
                data = response.json()
                
                result = {
                    "is_playing": data.get("is_playing", False),
                    "is_stopped": data.get("is_stopped", False),
                    "stop_reason": data.get("stop_reason"),
                    "current_loop": data.get("current_loop", 0),
                    "max_loops": data.get("max_loops", 0)
                }
                
                logger.info(f"Pod 상태 조회 성공: {pod_ip} - playing: {result['is_playing']}, stopped: {result['is_stopped']}")
                return result

        except httpx.RequestError as e:
            logger.error(f"Pod 상태 조회 실패 (RequestError): {pod_ip} - {str(e)}")
            return {
                "is_playing": False,
                "is_stopped": False,
                "stop_reason": "failed",
                "current_loop": 0,
                "max_loops": 0,
                "error": f"RequestError: {str(e)}"
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Pod 상태 조회 실패 (HTTPStatusError): {pod_ip} - {e.response.status_code}")
            return {
                "is_playing": False,
                "is_stopped": False,
                "stop_reason": "failed",
                "current_loop": 0,
                "max_loops": 0,
                "error": f"HTTPStatusError: {e.response.status_code} - {str(e)}"
            }
            
        except ValueError as e:
            logger.error(f"Pod 상태 조회 실패 (JSON 파싱 오류): {pod_ip} - {str(e)}")
            return {
                "is_playing": False,
                "is_stopped": False,
                "stop_reason": "failed",
                "current_loop": 0,
                "max_loops": 0,
                "error": f"ValueError: {str(e)}"
            }
            
        except Exception as e:
            logger.error(f"Pod 상태 조회 실패 (예상치 못한 오류): {pod_ip} - {type(e).__name__}: {str(e)}", exc_info=True)
            return {
                "is_playing": False,
                "is_stopped": False,
                "stop_reason": "failed",
                "current_loop": 0,
                "max_loops": 0,
                "error": f"Unexpected error: {type(e).__name__}: {str(e)}"
            }

    @staticmethod
    async def get_pod_rosbag_playing_status(pod_ip: str) -> str:
        """
        Dashboard용 Pod 상태 조회
        - 반환값: "pending", "running", "completed", "stopped", "failed"
        - "pending": rosbag 재생 준비 완료 (명령 대기 중)
        - "running": rosbag play 중
        - "completed": rosbag play 정상 완료
        - "stopped": rosbag play 사용자 중지
        - "failed": 조회 실패 또는 rosbag 실행 실패
        """
        if not pod_ip:
            return "pending"  # Pod 준비 전

        pod_api_url = f"http://{pod_ip}:8002/rosbag/status"

        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(pod_api_url)
                response.raise_for_status()
                data = response.json()
                print(f"응답 데이터: {data}")

                is_playing = data.get("is_playing", False)
                stop_reason = data.get("stop_reason")
                print(f"{pod_ip} 재생 상태 확인: {is_playing}, {stop_reason}")

                if is_playing:
                    # 재생 중: {"isPlaying": true, "stopReason": null} → "running"
                    return "running"
                else:
                    # isPlaying = false인 경우 stopReason으로 구분
                    if stop_reason == "completed":
                        # 정상 완료: {"isPlaying": false, "stopReason": "completed"} → "completed"
                        return "completed"
                    elif stop_reason == "user_stopped":
                        # 사용자 중지: {"isPlaying": false, "stopReason": "user_stopped"} → "stopped"
                        return "stopped"
                    elif stop_reason == "failed":
                        # 실행 실패: {"isPlaying": false, "stopReason": "failed"} → "failed"
                        return "failed"
                    elif stop_reason is None:
                        # 아직 시작 전: {"isPlaying": false, "stopReason": null} → "pending"
                        return "pending"
                    else:
                        # 알 수 없는 stopReason → "failed"
                        logger.warning(f"Unknown stopReason: {stop_reason}")
                        return "failed"

            except httpx.RequestError as e:
                logger.warning(f"Pod {pod_ip} rosbag 상태 조회 실패: {e}")
                return "failed"
            except Exception as e:
                logger.error(f"Pod {pod_ip} rosbag 상태 처리 오류: {e}")
                return "failed"

    @staticmethod
    async def send_post_request(pod_ip: str, endpoint: str, params: dict = None) -> dict:
        """
        비동기 POST 요청
        """
        if not pod_ip:
            raise HTTPException(status_code=400, detail="Pod IP is required")

        url = f"http://{pod_ip}:8002{endpoint}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, params=params)
                response.raise_for_status()

                try:
                    return response.json()
                except ValueError:
                    return {"message": response.text}

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise HTTPException(status_code=500, detail=f"Pod Server Request Failed: {e}")

    @staticmethod
    async def stop_rosbag(pod_ip: str) -> dict:
        """
        특정 Pod의 rosbag 재생을 중지 요청
        """
        return await RosService.send_post_request(pod_ip, "/rosbag/stop")
