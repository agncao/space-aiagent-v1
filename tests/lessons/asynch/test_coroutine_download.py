import asyncio
import time
from pathlib import Path

import aiohttp
import requests

urllist = [
    "https://mms-graph.cdn.bcebos.com/home-pc%2Ffood-2.jpeg",
    "https://mms-graph.cdn.bcebos.com/home-pc/emoji3.jpg",
    "https://mms-graph.cdn.bcebos.com/home-pc%2Fcar.jpg",
    "https://mms-graph.cdn.bcebos.com/home-pc%2Fhuman.jpg",
    "https://mms-graph.cdn.bcebos.com/home-pc%2Fplant.jpeg",
    "https://mms-graph.cdn.bcebos.com/home-pc%2Fscan.jpeg",
    "https://img.alicdn.com/imgextra/i3/O1CN01czcUm71Zge2U79oEw_!!6000000003224-2-tps-1344-1019.png",
    "https://img.alicdn.com/imgextra/i1/O1CN01sV1Fsl1EtpWkMdLux_!!6000000000410-2-tps-1740-600.png",
    "https://img.alicdn.com/imgextra/i3/O1CN019q6tqY29a4Y9eTxpW_!!6000000008083-2-tps-3480-1200.png",
    "https://img.alicdn.com/imgextra/i2/O1CN01uGqxxZ22Xb6CFCRpv_!!6000000007130-0-tps-5502-3812.jpg",
    "https://img.alicdn.com/imgextra/i3/O1CN010P8Pzl1CDZjGGI9xl_!!6000000000047-1-tps-480-144.gif",
    "https://img.alicdn.com/imgextra/i4/O1CN01rjCXEO1yXoR5lsMZO_!!6000000006589-0-tps-198-40.jpg",
    "https://gw.alicdn.com/imgextra/i4/O1CN012YkS1S20pKuSLCT05_!!6000000006898-0-tps-720-280.jpg",
    "https://img.alicdn.com/imgextra/i4/O1CN01Dc9PxV1XzlCMO1boO_!!6000000002995-2-tps-3456-864.png",
    "https://img.alicdn.com/imgextra/i3/O1CN01Q5eyfT1KawjDwueMh_!!6000000001181-2-tps-132-48.png",
    "https://img.alicdn.com/imgextra/i1/O1CN01KeJYQe1kaC9jYleaN_!!6000000004699-0-tps-704-170.jpg",
    "https://img.alicdn.com/imgextra/i3/O1CN01RVU96h1BwAHdgoMzK_!!6000000000009-2-tps-276-106.png",
    "https://hotax-public.oss-cn-zhangjiakou.aliyuncs.com/RULE_CENTER_PUB_5a12e7f14611258ce7a946e88419ee4d_87605a88-8987-4853-8c7d-d94c44db5c45.jpg",
    "https://hotax-public.oss-cn-zhangjiakou.aliyuncs.com/RULE_CENTER_PUB_e85ca139ea4b326c30a4d30c9cba8b61_1239ead6-89ee-4538-b46b-719195c0aee1.jpg",
    "https://hotax-public.oss-cn-zhangjiakou.aliyuncs.com/RULE_CENTER_PUB_1086e9ba2669339fdb8781005b81cbf9_39909912-2ba4-4df4-b3f2-18295936ac9a.jpg",
]

# 虽然方法已经是异步的，但requests.get() 是同步阻塞的 ，
# 所以 即使包在 asyncio.create_task() 里，下载本身也不会真正并发；
# 如果你想看到协程并发下载效果，需要换成 aiohttp / httpx.AsyncClient 这类异步 HTTP 客户端。
async def _download(task_name:str, url: str):
    print(f"{task_name} started...")
    resp = requests.get(url)
    file_name = url.split('/')[-1]
    with open(file_name, 'wb') as f:
        f.write(resp.content)
    print(f"{task_name} successfully.")

async def test_download():
    start = time.perf_counter()
    tasks =[asyncio.create_task(_download(f'task{i}',urllist[i]) ) for i in range(len(urllist)) ]
    await asyncio.wait(tasks)
    elapsed = time.perf_counter() - start
    print(f"All tasks completed in {elapsed:.2f}s")
# ======================================

async def _download_task(task_name: str, session: aiohttp.ClientSession, url: str) -> None:
    print(f"{task_name} started...")

    async with session.get(url) as resp:
        resp.raise_for_status()
        content = await resp.read()
        # file_name = url.rsplit("/", 1)[-1]
        # with open(file_name, 'wb') as f:
        #     f.write(content)
        # print(f"{task_name} successfully.")
    file_name = url.rsplit("/", 1)[-1]
    await asyncio.to_thread(Path(file_name).write_bytes, content)
    print(f"{task_name} successfully.")


async def test_download_task():
    start = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        tasks = [
            asyncio.create_task(_download_task(f"task{i}", session, url))
            for i, url in enumerate(urllist)
        ]
        await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - start
    print(f"All tasks completed in {elapsed:.2f}s")
