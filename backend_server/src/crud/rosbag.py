import requests
from fastapi import HTTPException

from src.utils.my_enum import PodStatus


class RosService:
    @staticmethod
    async def send_get_request(pod_ip):
        if pod_ip is None:
            return "Not Ready"

        pod_api_url = f"http://{pod_ip}:8002/rosbag/status"
        try:
            response = requests.get(pod_api_url)
            response.raise_for_status()
            response_data = response.json().get("status")

            if response_data == PodStatus.RUNNING.value:
                pod_status = PodStatus.RUNNING.value
            else:
                pod_status = PodStatus.STOPPED.value
        except requests.RequestException:
            return "Error"
        return pod_status

    @staticmethod
    async def send_post_request(pod_ip: str, endpoint: str, params: dict = None):
        try:
            url = f"http://{pod_ip}:8002{endpoint}"
            response = requests.post(url, params=params)
            response.raise_for_status()  # HTTP 오류 발생 시 예외 처리
            return response
        except requests.RequestException as e:
            raise HTTPException(status_code=500, detail=f"Pod Server Request Failed: {e}")
