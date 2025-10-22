#import uvicorn

#if __name__ == '__main__':
#    uvicorn.run("backend_server.src.main:app", host='127.0.0.1', port=8000, reload=True)

import sys
import os
import uvicorn

# backend_server/src 경로를 Python 경로에 추가
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend_server/src'))

if __name__ == '__main__':
    # 'main:app'으로 변경 (backend_server.src 부분 제거)
    uvicorn.run("main:app", host='127.0.0.1', port=8000, reload=True)
