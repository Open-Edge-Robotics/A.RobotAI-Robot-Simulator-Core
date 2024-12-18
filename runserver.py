import uvicorn

if __name__ == '__main__':
    uvicorn.run("backend_server.src.main:app", host='127.0.0.1', port=8000, reload=True)