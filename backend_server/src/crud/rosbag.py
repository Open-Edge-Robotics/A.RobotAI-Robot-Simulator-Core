import httpx
from fastapi import HTTPException


class RosService:
    @staticmethod
    async def get_pod_status(pod_ip: str) -> dict:
        """
        비동기 방식으로 Pod 상태 조회
        """
        if not pod_ip:
            return {
                "isPlaying": False,
                "current_loop": 0,
                "max_loops": 0,
                "error": "Not Ready"
            }

        pod_api_url = f"http://{pod_ip}:8002/rosbag/status"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(pod_api_url)
                response.raise_for_status()
                data = response.json()

                return {
                    "isPlaying": data.get("isPlaying", False),
                    "current_loop": data.get("current_loop", 0),
                    "max_loops": data.get("max_loops", 0)
                }

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
            return {
                "isPlaying": False,
                "current_loop": 0,
                "max_loops": 0,
                "error": str(e)
            }

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
