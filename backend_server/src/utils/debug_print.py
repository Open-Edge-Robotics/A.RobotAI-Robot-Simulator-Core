import asyncio
import threading
from datetime import datetime

def get_debug_info():
    """디버깅용 현재 컨텍스트 정보 반환"""
    return {
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "thread_id": threading.get_ident(),
        "task_name": getattr(asyncio.current_task(), 'name', 'unnamed') if asyncio.current_task() else 'no_task',
        "task_id": id(asyncio.current_task()) if asyncio.current_task() else 'no_task'
    }

def debug_print(message, **kwargs):
    """디버깅용 print 함수"""
    debug_info = get_debug_info()
    extra_info = f" | {kwargs}" if kwargs else ""
    print(f"🔍 [{debug_info['timestamp']}] [Thread:{debug_info['thread_id']}] [Task:{debug_info['task_name']}({debug_info['task_id']})] {message}{extra_info}", flush=True)
