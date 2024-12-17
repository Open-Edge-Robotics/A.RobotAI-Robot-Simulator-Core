from fastapi import FastAPI

from src.routes import rosbag

app = FastAPI()

app.include_router(rosbag.router)


@app.get("/")
async def root():
    return {"message": "Hello World"}
