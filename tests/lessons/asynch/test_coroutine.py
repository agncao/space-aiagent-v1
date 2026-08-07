import asyncio

import pytest


async def _func0():
    print("\n我是一个协程函数, 但是将我赋值给一个变量，那么这个变量就得到了一个协程变量")


def test_func0():
    cor = _func0()

    #  老的写法，python3.7以后 不这么写了
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(cor)

    # 这是 python3.7以后得写法
    # asyncio.run(_func0())     # 直接这么写也可以  _func0() 是协程对象
    asyncio.run(cor)


async def _func(task_name: str):
    print(f"{task_name} start")
    await asyncio.sleep(0)
    print(f"{task_name} end")


async def test_func():
    """Python 3.13 不再隐式创建 event loop，直接在 pytest 管理的 loop 中运行。"""
    tasks = [asyncio.create_task(_func(f"task_name{i}")) for i in range(2)]
    await asyncio.wait(tasks)


# 如果 pyproject.toml 中没有配置pytest-asyncio 的自动模式: asyncio_mode = "auto",
# 则必须加 @pytest.mark.asyncio
# asyncio_mode = "auto" pytest 会自动识别并用事件循环执行它们
# @pytest.mark.asyncio
async def test_func2():
    tasks = [asyncio.ensure_future(_func(f"task_name{i}")) for i in range(12)]

    await asyncio.wait(tasks)


# @pytest.mark.asyncio
async def test_func3():
    tasks = [asyncio.create_task(_func(f"task_name{i}")) for i in range(2)]

    await asyncio.wait(tasks)
