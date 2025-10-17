# utils.py
from typing import Any

def remove_none_recursive(obj: Any) -> Any:
    """
    중첩 dict, list에서 None 값 제거

    Args:
        obj: dict, list, 기타 객체
    Returns:
        None이 제거된 새로운 객체
    """
    if isinstance(obj, dict):
        return {k: remove_none_recursive(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_none_recursive(v) for v in obj if v is not None]
    else:
        return obj
