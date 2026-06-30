import inspect
import re

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool



def truncate(obj, max_len: int = 200, suffix: str = "...", truncate_hint: bool = True) -> str:
    """截断过长字符串
    Args:
        obj: 要截断的字符串
        max_len: 截断长度
        suffix: 截断后缀
        truncate_hint: 是否添加截断提示（默认添加截断提示）

    Returns:
        截断后的字符串
    """
    s = str(obj)
    if len(s) <= max_len:
        return s
    s1 = s[:max_len] + suffix
    return f"{s1}[截断, 总长{len(s)}]" if truncate_hint else s1



def camel_to_snake(name: str) -> str:
    """
    将驼峰命名法字符串转换为下划线分割的字符串

    Args:
        name: 驼峰命名的字符串

    Returns:
        下划线分割的字符串
    """
    # 处理连续大写字母的情况，如 HTTPServer -> http_server
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    # 处理小写字母或数字后跟大写字母的情况，如 myHTTPServer -> my_http_server
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def snake_to_camel(name: str) -> str:
    """
    将下划线分割的字符串转换为驼峰命名法字符串（小驼峰）

    Args:
        name: 下划线分割的字符串

    Returns:
        小驼峰命名的字符串
    """
    components = name.split('_')
    # 第一个组件保持小写，后续组件首字母大写
    return components[0] + ''.join(x.capitalize() for x in components[1:])

def keys_to_snake(data: dict) -> dict:
    """递归将字典中所有 key 从 camelCase 转换为 snake_case"""
    if not data:
        return {}
    result = {}
    for k, v in data.items():
        snake_key = camel_to_snake(k)
        if isinstance(v, dict):
            result[snake_key] = keys_to_snake(v)
        elif isinstance(v, list):
            result[snake_key] = [keys_to_snake(i) if isinstance(i, dict) else i for i in v]
        else:
            result[snake_key] = v
    return result


def args_to_camel(func, local_vars: dict, skip_none: bool = True) -> dict:
    """
    从函数参数中自动组装 camelCase 的 args dict。

    Args:
        func: 当前函数对象（@tool 装饰后的 StructuredTool 也行，自动取 coroutine/func）
        local_vars: locals() 的结果
        skip_none: 是否跳过值为 None 的参数（默认跳过）

    Returns:
        camelCase key 的参数字典，适合传给前端
    """
    # @tool 装饰后函数名被重新绑成 StructuredTool 实例，新版 langchain_core 的
    # StructuredTool 没有 __call__，直接 inspect.signature 会抛
    # "StructuredTool(...) is not a callable object"
    if isinstance(func, BaseTool):
        func = func.coroutine or func.func
    sig = inspect.signature(func)
    args = {}
    for param_name, param in sig.parameters.items():
        # 跳过 langgraph 注入的 ToolRuntime（不应传给前端）
        if param.annotation is ToolRuntime:
            continue
        value = local_vars.get(param_name)
        if skip_none and value is None:
            continue
        args[snake_to_camel(param_name)] = value
    return args

def flat_tuple_list(tuples_list: list[tuple[str,any]],element_split: str = ": ",join_str: str = ", ") -> str:
    """
    将元组列表展开为字符串:"k1:v1, k2:v2"
    
    """
    return join_str.join([f"{e1}{element_split}{e2}" for e1,e2 in tuples_list])
