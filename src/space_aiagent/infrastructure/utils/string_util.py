
def truncate(obj, max_len: int = 200) -> str:
    """截断过长字符串，用于日志输出"""
    s = str(obj)
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...[截断, 总长{len(s)}]"
