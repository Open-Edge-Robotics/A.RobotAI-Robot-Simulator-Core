from fastapi import APIRouter, HTTPException
from starlette import status

from pod_server.src.crud.rosbag2 import RosbagService

router = APIRouter(prefix="/rosbag", tags=["Rosbag"])

rosbag_service = RosbagService()

@router.post("/play", status_code=status.HTTP_202_ACCEPTED)
async def rosbag_play(object_path: str):
    # print("재생 시작")
    # await rosbag_service.play_rosbag(object_path)
    # return {"message": "Rosbag play started"}
    try:
        result = await rosbag_service.play_rosbag(object_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/stop", status_code=status.HTTP_200_OK)
async def rosbag_stop():
    return await rosbag_service.stop_rosbag()

@router.get("/status", status_code=status.HTTP_200_OK)
async def rosbag_status():
    return await rosbag_service.get_status()