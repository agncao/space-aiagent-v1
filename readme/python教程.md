# Space AIAgent Python 实战教程

> 本教程基于 **space-aiagent** 项目（航天分析平台智能助手）的实际代码，整理了项目中用到的 Python 语言特性和第三方包。适合有 Java/Spring Boot 背景、正在学习 Python 进行企业级 AI Agent 开发的工程师。

---

## 目录

1. [项目概览](#1-项目概览)
2. [Python 语言特性](#2-python-语言特性)
3. [Web 框架：FastAPI + uvicorn](#3-web-框架fastapi--uvicorn)
4. [数据验证：Pydantic](#4-数据验证pydantic)
5. [配置管理：pydantic-settings + YAML + .env](#5-配置管理pydantic-settings--yaml--env)
6. [结构化日志：structlog](#6-结构化日志structlog)
7. [LLM 接入：langchain-openai](#7-llm-接入langchain-openai)
8. [Agent 框架：deepagents + langgraph](#8-agent-框架deepagents--langgraph)
9. [Agent 调用模式：invoke vs stream vs astream_events](#9-agent-调用模式invoke-vs-stream-vs-astream_events)
10. [工具定义：langchain_core.tools](#10-工具定义langchain_coretools)
11. [异步桥接：asyncio.Future + ContextVar](#11-异步桥接asynciofuture--contextvar)
12. [CLI 工具：click](#12-cli-工具click)
13. [数据库：aiosqlite](#13-数据库aiosqlite)
14. [代码质量：ruff + pre-commit](#14-代码质量ruff--pre-commit)
15. [测试：pytest + pytest-asyncio](#15-测试pytest--pytest-asyncio)
16. [项目结构最佳实践](#16-项目结构最佳实践)
17. [AgentMiddleware 中间件深度讲解](#17-agentmiddleware-中间件深度讲解)
18. [Agent 结构化输出：response_format 详解](#18-agent-结构化输出response_format-详解)
19. [标准库：os 模块常用方法](#19-标准库os-模块常用方法)
20. [Python 包机制：`__init__.py` 与模块导入](#20-python-包机制__init__py-与模块导入)
21. [Python 魔术方法（Dunder Methods）](#21-python-魔术方法dunder-methods)
22. [动态加载与反射机制（Skill 案例剖析）](#22-动态加载与反射机制skill-案例剖析)
23. [DeepAgents `context_schema` 源码解析](#23-deepagents-context_schema-源码解析)
24. [`create_deep_agent` 参数全解析（源码级）](#24-create_deep_agent-参数全解析源码级)
25. [LangGraph Command —— 状态更新 + 控制流导航](#25-langgraph-command--状态更新--控制流导航)

---

## 1. 项目概览

### 1.1 项目架构一览

```
前端 Cesium (JS) ← WebSocket → 后端 FastAPI (Python) → DeepAgents 多 Agent 系统
```

**核心链路**：用户在前端输入自然语言 → WebSocket 发送到后端 → Agent 理解意图 → 生成工具调用指令 → WebSocket 返回前端 → 前端调用 Cesium API 执行。

### 1.2 技术栈总览

| 类别 | 包名 | 在本项目中的用途 |
|------|------|-----------------|
| Web 框架 | `fastapi` | HTTP/WebSocket 服务 |
| 服务器 | `uvicorn` | ASGI 服务器，运行 FastAPI |
| 数据校验 | `pydantic` | WebSocket 消息模型、工具参数校验 |
| 配置 | `pydantic-settings` + `pyyaml` + `python-dotenv` | 多环境配置管理 |
| 日志 | `structlog` | 结构化 JSON 日志 |
| LLM | `langchain-openai` | DeepSeek / Qwen 接口调用 |
| Agent | `deepagents` + `langgraph` | 主控 Agent + 子 Agent 编排 |
| 工具 | `langchain-core` | `@tool` 装饰器定义工具函数 |
| CLI | `click` | `space-aiagent` 命令行 |
| 数据库 | `aiosqlite` | SQLite 异步持久化 |
| 代码质量 | `ruff` + `pre-commit` | 格式化和 lint |

---

## 2. Python 语言特性

本节介绍项目中用到的 Python 核心语言特性，适合有 Java 背景的开发者快速建立映射。

### 2.1 类型注解（Type Hints）

Python 3.5+ 支持类型注解，本项目强制要求所有函数参数和返回值都有类型注解。

```python
# ✅ 项目中的写法
def _build_skill_summaries(registry: SkillRegistry) -> str:
    """构建 Skill 摘要文本"""
    summaries = registry.get_summaries()
    if not summaries:
        return "（暂无可用 Skill）"
    return "\n".join(f"- {s['name']}: {s['description']}" for s in summaries)

# ✅ async 方法也需要类型注解
async def send_tool_call(
    self,
    tool_func: str,
    args: dict,
    timeout: float = 60,
) -> dict:
    ...
```

**Java 对比**：

```python
# Python 可选类型
def get_user(name: str | None = None) -> dict | None:   # Python 3.10+
    ...

# Java 等价
# public Optional<Map<String, Object>> getUser(@Nullable String name)
```

**常用类型注解速查**：

```python
from typing import Any
from pathlib import Path

# 基本类型
name: str = "hello"
count: int = 42
active: bool = True

# 容器类型
names: list[str] = ["a", "b"]        # Python 3.9+
config: dict[str, Any] = {"key": 1}
ids: set[int] = {1, 2, 3}
pair: tuple[str, int] = ("x", 1)

# 可选类型（Python 3.10+ 推荐用 | None）
value: str | None = None             # 等价于 Optional[str]
items: list[int] | None = None

# 联合类型
result: dict | list | None = None    # 可以是三种类型之一

# 复杂嵌套
data: dict[str, list[dict[str, Any]]] = {}
```

### 2.2 `async` / `await` — 协程与异步 I/O

Python 的异步模型本质上和 Java NIO / Netty 是**同一件事** — 都是事件驱动（event-driven）+ 非阻塞 I/O，只是一个用协程表达，一个用回调/Selector 表达。

#### 2.2.1 先理解根本区别：协程 vs 线程

**Java 线程模型（1:1 映射到 OS 线程）**：

```
┌─────────────────────────────────────────────────┐
│  1000 个并发请求                                 │
│  ┌──────┐ ┌──────┐ ┌──────┐      ┌──────┐     │
│  │线程1 │ │线程2 │ │线程3 │ ...  │线程N │     │
│  │  ↓   │ │  ↓   │ │  ↓   │      │  ↓   │     │
│  │阻塞等│ │阻塞等│ │阻塞等│      │阻塞等│     │
│  │DB响应│ │HTTP  │ │文件IO│      │ ...  │     │
│  └──────┘ └──────┘ └──────┘      └──────┘     │
│  每个线程 ≈ 1MB 栈内存                          │
│  1000 线程 = 1GB 内存                           │
│  线程切换 = 内核态上下文切换（昂贵）              │
└─────────────────────────────────────────────────┘
```

**Python 协程模型（M:1，多个协程在单个线程上调度）**：

```
┌─────────────────────────────────────────────────┐
│  1000 个并发请求                                 │
│  ┌──────────────────────────────────────────┐  │
│  │              单线程事件循环                │  │
│  │  Task1 ──→ Task2 ──→ Task3 ──→ Task1 ──→  │  │
│  │    │         │         │         │         │  │
│  │  await     await     await     await       │  │
│  │  DB查询    HTTP请求   文件IO    DB返回      │  │
│  └──────────────────────────────────────────┘  │
│  内存开销 ≈ 几 KB / 协程                         │
│  1000 协程 ≈ 几 MB 内存                          │
│  上下文切换 = 函数调用级别（轻量）                 │
└─────────────────────────────────────────────────┘
```

**一句话总结**：

| | 线程 (Java) | 协程 (Python) |
|---|---|---|
| 调度者 | 操作系统内核 | 用户态事件循环 |
| 切换成本 | ~1-10μs（系统调用） | ~100ns（函数调用） |
| 内存开销 | ~1MB/线程 | ~几KB/协程 |
| 并发上限 | 几百~几千 | 几万~几十万 |
| 抢占方式 | 抢占式（OS 决定什么时候切） | 协作式（`await` 处主动让出） |
| 适合场景 | CPU 密集型 | I/O 密集型 |

#### 2.2.2 Python 事件循环 = Java 的 Netty EventLoop

这是最关键的理解：**Python 的 `asyncio` 事件循环，和 Netty 的 `EventLoop` 是同一套思想**。

```java
// Java Netty 方式（回调风格）
public class NettyHandler extends ChannelInboundHandlerAdapter {
    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) {
        // msg 来了 → 处理 → 通过回调链传递结果
        doSomethingAsync(msg).addListener(future -> {
            if (future.isSuccess()) {
                ctx.writeAndFlush(future.get());
            }
        });
    }
}
```

```python
# Python asyncio 方式（协程风格 — 同样的事件循环，但是回调变成了 await）
async def handle_message(websocket: WebSocket):
    raw = await websocket.receive_text()    # 等价于 Netty 的 channelRead 触发
    result = await do_something_async(raw)  # 等价于 addListener 回调
    await websocket.send_json(result)       # 等价于 writeAndFlush
```

**Python 比 Netty 好在哪？** 不是性能更好，而是**表达更直观**：回调地狱变成了顺序书写的 `await`。

#### 2.2.3 Python 高性能网络栈

本项目的网络 I/O 栈：

```
┌──────────────────────────────────────────┐
│  space-aiagent (你的代码)                  │
│  async def websocket_endpoint(...)        │  ← 协程风格业务代码
├──────────────────────────────────────────┤
│  FastAPI / Starlette                      │  ← ASGI 框架
├──────────────────────────────────────────┤
│  uvicorn                                  │  ← ASGI 服务器，管理事件循环
│  └─ 默认使用 uvloop（下面细说）              │
├──────────────────────────────────────────┤
│  uvloop (libuv 的 Python 绑定)            │  ← 高性能事件循环
│  └─ 用 C 写的，比标准 asyncio 快 2-4 倍     │
├──────────────────────────────────────────┤
│  OS 层 (epoll / kqueue)                   │  ← 和 Java NIO Selector 同一个东西
└──────────────────────────────────────────┘
```

**uvloop — Python 的 "Netty 内核"**：

`uvloop` 是基于 [libuv](http://libuv.org/)（Node.js 的事件循环引擎）的 Python 事件循环替代品。**uvicorn 检测到 `uvloop` 已安装时会自动使用它**，无需任何配置。

```bash
# uvicorn[standard] 已经包含了 uvloop 依赖
# 本项目的 pyproject.toml 中：
# "uvicorn[standard]>=0.49.0"
```

它的性能接近 Go/Node.js 水平：

```
基准测试（每秒处理请求数）：
┌────────────────┬──────────────┐
│ asyncio (标准)  │ ~30,000 QPS  │
│ uvloop          │ ~80,000 QPS  │
│ Node.js (libuv) │ ~70,000 QPS  │
│ Go net/http     │ ~85,000 QPS  │
└────────────────┴──────────────┘
```

#### 2.2.4 本项目中的协程实践

**场景一：WebSocket + Future 桥接（`api/websocket.py` + `bridge/ws_bridge.py`）**

这是本项目最核心的异步模式——**一个 WebSocket 连接同时处理"收"和"发"两条异步路径**：

```python
# 路径1: WebSocket handler 等待前端消息
@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    while True:
        raw = await websocket.receive_text()   # ← 协程挂起点1：等前端发消息
        # ... 处理 user_input 或 tool_result


# 路径2: 工具函数等待前端执行结果（同一连接）
@tool
async def create_scenario(name: str = "新建场景") -> dict:
    bridge = bridge_var.get()
    # send_tool_call 内部：
    #   1. 通过 WebSocket 发送指令（立即返回）
    #   2. 创建 Future
    #   3. await future  ← 协程挂起点2：等前端返回 tool_result
    result = await bridge.send_tool_call("createScenario", args={"name": name})
    return result
```

**时序图**（同一个线程，两个协程交替执行）：

```
时间轴 →
        协程A (websocket handler)          协程B (工具函数)
        ────────────────────────          ───────────────
t1:     receive_text() → 挂起（等前端）
t2:                                        收到 user_input → Agent 执行
t3:                                        调用 create_scenario
t4:                                        send_json 发送 tool_call
t5:                                        await future → 挂起（等前端结果）
t6:     收到 tool_result → 唤醒协程A
t7:     resolve(future) → 唤醒协程B ────────→ 拿到结果，继续执行
t8:     receive_text() → 挂起（等下一个消息）
t9:                                        Agent 执行完毕，发送 ai_message
```

**场景二：Agent 链式调用（`agents/orchestrator.py`）**

```python
# Agent 内部可能经过 多轮 tool_call → tool_result 的往返
# 每一轮都是一次 await 挂起/恢复
result = await agent.ainvoke(
    {"messages": [HumanMessage(content="帮我创建一颗卫星")]},
    config={"configurable": {"thread_id": "abc-123"}},
)
# agent.ainvoke 内部可能会:
#   await tool_1() → 发指令到前端 → 等结果
#   await tool_2() → 发指令到前端 → 等结果
#   ... 多轮 ...
#   最后返回 AI 文本回复
```

**场景三：异步数据库（`infrastructure/database.py`）**

```python
# SQLite 是磁盘 I/O，用 aiosqlite 避免阻塞事件循环
async def initialize(self) -> None:
    self._db = await aiosqlite.connect(str(self.db_path))  # await 磁盘 I/O
    await self._db.execute("PRAGMA journal_mode=WAL")       # await 磁盘 I/O
```

#### 2.2.5 Python 异步核心 API 速查

```python
import asyncio

# 1. 并行执行多个协程
results = await asyncio.gather(
    fetch_user(1),
    fetch_order(2),
    fetch_log(3),
)
# 等价于 Java CompletableFuture.allOf()

# 2. 创建后台任务（不等待）
task = asyncio.create_task(send_notification("hello"))
# 等价于 CompletableFuture.supplyAsync(() -> ...)

# 3. 超时控制
try:
    result = await asyncio.wait_for(slow_operation(), timeout=10.0)
except TimeoutError:
    result = "timeout"

# 4. 线程池中运行同步代码（CPU 密集型或阻塞 I/O）
result = await asyncio.to_thread(blocking_cpu_task, arg1, arg2)
# 把同步函数丢到线程池执行，不阻塞事件循环

# 5. Future（低级 API，WSBridge 中用到了）
loop = asyncio.get_event_loop()
future = loop.create_future()
# ... 在别处 ...
future.set_result("done")     # resolve
# 等价于 CompletableFuture + complete()
```

#### 2.2.6 常见陷阱

```python
# ❌ 陷阱1: 在 async 函数里调用同步阻塞代码
async def bad():
    time.sleep(5)          # 整个事件循环卡 5 秒！
    await do_something()

# ✅ 正确: 用 asyncio.to_thread 或 asyncio.sleep
async def good():
    await asyncio.to_thread(time.sleep, 5)   # 放到线程池
    # 或者直接用异步版本的 sleep
    await asyncio.sleep(5)


# ❌ 陷阱2: 忘记 await
async def forget():
    fetch_data(url)        # 返回一个 coroutine 对象，但没执行！
    print("done")          # 这条会立刻打印，fetch_data 根本没跑


# ❌ 陷阱3: CPU 密集计算阻塞事件循环
async def cpu_heavy():
    for i in range(100_000_000):
        x = i * i          # 纯计算，没有 await → 事件循环卡住
    # ✅ 正确: await asyncio.to_thread(cpu_heavy_sync)
```

#### 2.2.7 Java NIO/Netty 开发者迁移速查

| 你想做的事 | Java Netty 写法 | Python asyncio 写法 |
|---|---|---|
| 创建事件循环组 | `new NioEventLoopGroup(n)` | 自动，单线程事件循环 |
| 绑定端口 | `b.bind(port).sync()` | `uvicorn.run(app, port=8028)` |
| 收到消息 | `channelRead(ctx, msg)` | `data = await ws.receive_text()` |
| 发送消息 | `ctx.writeAndFlush(msg)` | `await ws.send_json(data)` |
| 异步操作完成回调 | `.addListener(f -> {...})` | `result = await future` |
| 超时控制 | `ctx.executor().schedule(...)` | `await asyncio.wait_for(task, timeout)` |
| 后台定时任务 | `eventLoop.scheduleAtFixedRate(...)` | `asyncio.create_task(periodic())` |
| 关闭 | `group.shutdownGracefully()` | 自动，进程退出时清理 |

**一句话总结**：Java Netty 用 EventLoopGroup + ChannelHandler 回调 → Python 用 `async def` + `await`，**底层都是 epoll，上层表达不同**。Python 把回调链展开成了顺序代码。

### 2.3 `dataclass` — 数据类

Python 的 `dataclass` 类似 Java 的 `@Data` (Lombok) 或 `record`。

```python
from dataclasses import dataclass, field
from pathlib import Path

# 项目中的实际用法：Skill 元信息
@dataclass
class SkillInfo:
    """单个 Skill 的元信息"""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)      # 可变默认值必须用 default_factory
    skill_dir: Path = field(default_factory=Path)
    _tools: list | None = field(default=None, repr=False)   # 私有字段不计入 repr
```

**`field()` 常用参数**：

| 参数 | 含义 |
|------|------|
| `default` | 默认值（不可变类型） |
| `default_factory` | 默认值工厂函数（可变类型如 `list`/`dict` 必须用这个） |
| `repr=False` | 不显示在 `__repr__` 中 |
| `init=False` | 不显示在 `__init__` 参数中 |

### 2.4 `StrEnum` — 字符串枚举

Python 3.11 新增 `StrEnum`，枚举值可直接当字符串用。

```python
from enum import StrEnum

# 项目中的实际用法
class EntityType(StrEnum):
    """场景实体类型"""
    PLACE = "place"
    SATELLITE = "satellite"
    MISSILE = "missile"
    SHIP = "ship"

class WSMessageType(StrEnum):
    """WebSocket 消息类型"""
    USER_INPUT = "user_input"
    TOOL_RESULT = "tool_result"
    AI_MESSAGE = "ai_message"
    TOOL_CALL = "tool_call"
    END = "end"
    ERROR = "error"

# 使用方式
msg_type = WSMessageType.USER_INPUT
print(msg_type)               # "user_input"  -- 自动转字符串
print(msg_type == "user_input")  # True
```

> **注意**：`StrEnum` 是 Python 3.11 的新特性。本项目要求 Python 3.13，所以可以直接使用。

### 2.5 `ContextVar` — 上下文变量

Python 3.7 引入的 `ContextVar` 是线程/协程安全的变量存储，类似 Java 的 `ThreadLocal`，但支持 **async/await 上下文自动传播**。

```python
from contextvars import ContextVar

# 项目中的实际用法：注入当前会话的 WSBridge
# 文件: bridge/__init__.py
bridge_var: ContextVar[WSBridge | None] = ContextVar("bridge_var", default=None)


# 在 WebSocket handler 中设置（注入当前会话的 bridge）
# 文件: api/websocket.py
bridge = session_manager.register(current_thread_id, websocket)
token = bridge_var.set(bridge)

try:
    # 在 Agent 执行期间，所有工具函数都可以通过 get() 获取 bridge
    result = await agent.ainvoke(...)
finally:
    bridge_var.reset(token)    # 恢复原值


# 在工具函数中获取（任意嵌套深度）
# 文件: skills/scene_management/tools.py
@tool
async def create_scenario(name: str = "新建场景") -> dict:
    bridge = bridge_var.get()   # 获取当前会话的 bridge
    if bridge is None:
        return {"success": False, "message": "bridge 未注入"}
    result = await bridge.send_tool_call("createScenario", args={"name": name})
    return result
```

**为什么不用全局变量？** 多个前端用户同时连接时，每个 `thread_id` 对应不同的 WebSocket 连接。`ContextVar` 确保每次 Agent 调用都能拿到**属于自己的那个** bridge，不会串。

#### 2.5.1 进阶：ContextVar 底层机制拆解（HAMT + Copy-on-Write）

上面是工程视角的用法。若想知道**为什么 `asyncio.create_task` 会复制 Context**、**为什么 token 跨 Context 会抛 ValueError**（详见 [11.6 节](#116-contextvar-跨-context-错误详解)），必须理解 `ContextVar` 的底层内存模型。本节内容覆盖 CSDN 文章 [《Python ContextVar 底层机制与内存模型拆解》](https://blog.csdn.net/qq_37510030/article/details/161948579) 的核心知识点。

##### A. 三层职责分离：Key / Context / Context Stack

CPython 的 `contextvars` 模块把"上下文变量"这件事拆成**三个互不重叠的角色**，不像 Java `ThreadLocal` 那样把"key + 存储 + 路由"都揉进一个 `Map<Thread, T>` 里。

| 角色 | 类型 | 职责 | 类比 |
|------|------|------|------|
| **Key** | `ContextVar` | 只是一个**对象身份**（`id()`），本身不存值 | `dict` 的 `key`（但不参与 hash 表） |
| **Context** | `Context` | 一个"协程私有的 HAMT 字典"，存储 `ContextVar → Token/value` 映射 | 线程私有存储 |
| **Context Stack** | 解释器维护的栈 | 路由：当前哪个协程在跑 → 用哪个 Context | 调度器查询当前线程 |

```python
from contextvars import ContextVar

# ContextVar 本身只是 Key，没有 value 字段
bridge_var: ContextVar[str | None] = ContextVar("bridge_var", default=None)

# 真正的存储在哪？在"当前 Context 对象"的 HAMT 树里
import contextvars
ctx: contextvars.Context = contextvars.copy_context()   # 拿到当前 Context
# ctx[bridge_var] → 通过 Key 查 HAMT 树取值
```

**关键认知**：
- `ContextVar` 实例的 `name` 字段（字符串 "bridge_var"）**只是给人看的**，运行时不参与查找。`ContextVar` 内部用 `id()`（对象身份）作为 HAMT 的键
- 两个同名 `ContextVar` 是**两个不同的 Key**，互不干扰

```python
# ⚠️ 两个同名 ContextVar 是不同的 Key
a = ContextVar("x")
b = ContextVar("x")
a.set(1)
print(b.get())   # 不会拿到 1，会抛 LookupError（b 没有 set 过）或返回 default
```

##### B. HAMT 树：为什么 Context 创建是 O(1)

`Context` 的内部不是 Python `dict`，而是 **HAMT（Hash Array Mapped Trie）**——一棵**不可变**的 Trie 树。这意味着：

- **任何"修改"操作都会产生一棵新的树**，旧的树原地不动（不可变持久化数据结构）
- 新树**复用旧树 99% 的节点**，只复制从根到被改节点路径上的少数几个节点
- 这是 **Copy-on-Write** 的极致实现

```
set 操作（简化伪代码）：

def ContextVar.set(value):
    token = Token(self, old_value, current_context)
    # ⚠️ 关键：current_context 是当前的 Context 引用
    new_context = current_context._hamt.assoc(self, value)
    # 把当前 Context Stack 顶端的 Context 替换成 new_context
    _set_current_context(new_context)
    return token
```

```
get 操作（简化伪代码）：

def ContextVar.get():
    ctx = _get_current_context()    # 从 Context Stack 顶部取
    if self in ctx._hamt:
        return ctx._hamt[self]
    if self._default is not MISSING:
        return self._default
    raise LookupError
```

##### C. `asyncio.create_task` 为什么复制 Context：O(1) 的子协程诞生

```python
import asyncio

async def child():
    print(bridge_var.get())    # 子协程看到的值

async def parent():
    bridge_var.set("parent_bridge")
    task = asyncio.create_task(child())   # ← 这里复制 Context
    await task
```

**`create_task` 内部做了什么**：

```
1. 调用 contextvars.copy_context()
   └─ copy_context() 不是真的"深拷贝"，因为 HAMT 不可变
      只需返回当前 Context 对象引用 → O(1)！
      （HAMT 的不可变性 + Copy-on-Write 让"复制"退化为"共享根节点"）

2. 把 child 协程绑定到这个 Context 上
   └─ task._coro = child.__wrapped__，使其运行时用这个 Context

3. 调度到事件循环时，把 task 的 Context push 到 Context Stack
```

**O(1) 的来源**：

| 方案 | 复杂度 | 原因 |
|------|--------|------|
| Java `ThreadLocal` 在线程间复制 | O(N)（N = ThreadLocal 数量） | 必须 enumerate 每个 entry |
| Python `Context` 复制 | **O(1)** | HAMT 不可变，复制 = 共享根节点引用 |

所以 Python 协程**几千个 task 同时跑**也几乎不耗内存——它们共享同一棵 HAMT 树，只有真正 `set` 的字段才会触发 COW 复制路径节点。

##### D. Token 跨 Context 为什么抛 ValueError

回头看 [11.6 节](#116-contextvar-跨-context-错误详解)的 bug：

```python
async def parent():
    token = bridge_var.set(bridge)         # ① token 在 parent 的 Context 生成
    task = asyncio.create_task(child(token))   # ② child 用的是 parent Context 的副本

async def child(token):
    bridge_var.reset(token)                # ③ 在 child Context 中 reset parent 的 token
    # 💥 ValueError: <Token var=<ContextVar ...> was created in a different Context
```

`Token` 内部记录了**生成它的 Context 引用**：

```python
# Token 的逻辑结构
class Token:
    var: ContextVar              # 哪个 ContextVar
    old_value: Any               # set 之前的值
    used: bool                   # 是否已 reset
    # ⚠️ 没有 context 字段，但在 C 实现里隐式绑定
```

`reset(token)` 时 CPython 会检查"token 是不是当前 Context 生成的"。如果 `create_task` 复制了 Context（新对象 = 新 `id()`），C 检查就失败。

**根因**：HAMT 节点不可变，所以"修改当前 Context"实际是替换 `Context Stack` 顶端的引用；token 必须知道"我属于哪个引用"，否则无法精确撤销。

##### E. 内存模型速查图

```
进程内存
├── ContextVar 池（id → ContextVar 对象）
│     bridge_var @ 0x7f00...001
│     scene_name_var @ 0x7f00...002
│
├── Context Stack（每条 asyncio 线程一条）
│     [Top] parent_ctx (HAMT 树根)
│            ↓ create_task
│            child_ctx (HAMT 树根 - 和 parent_ctx 共享 99% 节点)
│
└── HAMT 树节点（不可变）
      Root → Trie Level 0 → Trie Level 1 → ... → Leaf(value)
      每次 set 走 COW：根 → 路径上的节点复制，其他节点共享
```

##### F. 工程实践要点

| 实践 | 原因 |
|------|------|
| `set()` 和 `reset(token)` 放在**同一个 `async def`** | 不要被 `asyncio.create_task` 隔开 |
| ContextVar 实例在**模块级**创建一次 | 同名多次创建会变成不同的 Key |
| 不要把 ContextVar 跨进程传递 | Context 不跨进程，多进程要重新 set |
| `asyncio.run(main())` 会为 main 创建独立 Context | 所以 `asyncio.run` 是 Context 边界 |
| 测试中直接 `bridge_var.set(...)` 即可 | pytest-asyncio 给每个 test 创建独立 task = 独立 Context |

##### G. 与 Java ThreadLocal 的关键差异

| 维度 | Java ThreadLocal | Python ContextVar |
|------|------------------|-------------------|
| 作用域 | 线程 | 协程（async 函数） |
| 路由方式 | `Thread.currentThread()` | 解释器维护的 Context Stack |
| 子作用域继承 | `InheritableThreadLocal` 手动复制 | `asyncio.create_task` **自动** O(1) 复制 |
| 显式撤销 | 无对应 API | `reset(token)` 精确恢复 |
| 跨作用域 reset | 不适用 | **不允许**（抛 ValueError） |
| 内存开销 | 线程数 × ThreadLocal 数 × 值 | 共享 HAMT，只有修改路径节点存在 |

### 2.6 `pathlib.Path` — 路径操作

Python 3.4 引入的现代路径库，替代 `os.path`。

```python
from pathlib import Path

# 项目中的实际用法
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"    # 用 / 拼接路径
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

# 读取文本文件
template = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")

# 检查文件/目录
yaml_path = child / "skill.yaml"
if not yaml_path.exists():
    continue
if not SKILLS_DIR.is_dir():
    return

# 遍历目录
for child in sorted(SKILLS_DIR.iterdir()):
    if child.is_dir():
        print(child.name)

# 创建目录
log_path = Path("./logs")
log_path.mkdir(parents=True, exist_ok=True)   # 类似 mkdir -p
```

**Java 对比**：

```python
from pathlib import Path

# Python
config_dir = Path(__file__).parent / "config"
text = config_dir.read_text(encoding="utf-8")

# Java
# Path configDir = Path.of(MyClass.class.getResource("/config")).toPath();
# String text = Files.readString(configDir.resolve("settings.txt"));
```

### 2.7 推导式（Comprehension）

Python 的列表/字典推导式是项目中大量使用的简洁语法。

```python
# 列表推导式 — 从列表生成列表（等价于 Java Stream.map）
names: list[str] = [s["name"] for s in summaries]

# 过滤 + 转换（等价于 Java filter + map）
active_skills = [s for s in summaries if s.get("enabled")]

# 字典推导式
mapping: dict[str, int] = {s["name"]: len(s["description"]) for s in summaries}

# 项目实例：生成 Skill 摘要
return [
    {"name": info.name, "description": info.description}
    for info in self._skills.values()
]

# 项目实例：拼接多行字符串
return "\n".join(f"- {s['name']}: {s['description']}" for s in summaries)
```

### 2.8 f-string — 格式化字符串

Python 3.6 引入，类似 Java 的 `String.format()` 或 JS 的模板字符串。

```python
# 类型安全的日志
logger.info("共注册 %d 个 Skill: %s", len(self._skills), list(self._skills.keys()))

# 项目中的实际用法
module_name = f"space_aiagent.skills.{skill_dir.name}.tools"
db_url = f"sqlite+aiosqlite:///{db_dir}/space_aiagent.db"
return f"{record.filename}:{record.lineno}"

# {} 中可以是任意表达式
logger.info(f"Skill [%s]: %d 个工具", skill_name, len(tools))
```

### 2.9 `importlib.util` — 动态导入

`importlib` 是 Python 的"反射"机制。项目中用它实现 **Skill 动态加载** — 运行时根据 YAML 配置导入 tools.py。

```python
import importlib.util
from pathlib import Path

# 项目中的实际用法
def _import_tools_module(self, skill_dir: Path, skill_name: str):
    tools_path = skill_dir / "tools.py"
    if not tools_path.exists():
        return None

    # 第1步：创建模块规格（类似 Java Class.forName）
    module_name = f"space_aiagent.skills.{skill_dir.name}.tools"
    spec = importlib.util.spec_from_file_location(module_name, str(tools_path))
    if spec is None or spec.loader is None:
        return None

    # 第2步：创建空模块对象
    module = importlib.util.module_from_spec(spec)

    # 第3步：执行模块代码（类似 Java 中加载类）
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        logger.exception("导入 tools.py 失败: %s", tools_path)
        return None


# 使用：
module = self._import_tools_module(info.skill_dir, skill_name)
# 然后遍历模块属性，找出 @tool 装饰的函数
for attr_name in dir(module):
    attr = getattr(module, attr_name)
    if isinstance(attr, BaseTool):
        tools.append(attr)
```

**Java 类比**：

```python
# Python: 动态导入
module = importlib.import_module("space_aiagent.skills.scene_management.tools")
tools = [getattr(module, name) for name in dir(module) if isinstance(getattr(module, name), BaseTool)]

# Java: 反射
# Class<?> clazz = Class.forName("com.example.skills.SceneManagement");
# Object instance = clazz.getDeclaredConstructor().newInstance();
# for (Method m : clazz.getDeclaredMethods()) { ... }
```

### 2.10 单例模式

Python 中通常用模块级变量实现单例。

```python
# 全局配置单例
_settings: Settings | None = None    # 模块级私有变量

def get_settings() -> Settings:
    """获取全局配置单例"""
    global _settings                    # 声明要修改模块级变量
    if _settings is None:
        load_dotenv(PROJECT_ROOT / ".env")
        env = os.getenv("APP_ENV", "dev")
        yaml_config = _load_yaml_config(env)
        _settings = _apply_yaml_to_settings(yaml_config)
    return _settings


# Agent 缓存（也是单例模式）
_agent_cache: dict[str, object] = {}

async def _get_or_create_agent(thread_id: str):
    """获取或创建指定 thread 的 Agent 实例（异步，需要数据库初始化）"""
    if thread_id in _agent_cache:
        return _agent_cache[thread_id]

    # 获取 checkpointer（SQLite 持久化）
    checkpointer = await _get_checkpointer()
    agent = create_orchestrator(subagents, loader, checkpointer)
    _agent_cache[thread_id] = agent
    return agent
```

### 2.11 `global` — 模块级变量声明

`global` 用于在**函数内部**声明某个变量是**模块级别的全局变量**，而不是函数内的局部变量。

**核心规则**：

```python
x = 10  # 模块级全局变量

def foo():
    x = 20       # 没有 global → 创建的是局部变量 x，不影响全局 x

foo()
print(x)         # 10  ← 全局 x 没变
```

```python
x = 10

def foo():
    global x     # 声明"我要修改全局的 x"
    x = 20       # 修改的是全局 x

foo()
print(x)         # 20  ← 全局 x 被改了
```

**本项目中的三个实际用法**（都在单例模式的 `if _xxx is None: _xxx = ...` 场景）：

| 文件 | 代码 | 作用 |
|------|------|------|
| `infrastructure/config.py` | `global _settings` → `_settings = ...` | 创建配置单例后回写到模块级变量 |
| `infrastructure/database.py` | `global _db` → `_db = Database(...)` | 创建数据库连接单例后回写 |
| `api/websocket.py` | `global _registry, _skill_loader` | 一次性声明多个全局变量 |

**常见误区**：

```python
# ❗误区一：只读不需要 global
x = 10

def bar():
    print(x)   # ✅ 只读全局变量，不需要 global


# ❗误区二：修改可变对象的内部不需要 global
cache: dict[str, object] = {}

def add_to_cache(key: str, val: object):
    # ✅ 不需要 global！cache 指向的对象没变，只是改了对象内部
    cache[key] = val


# ❗误区三：重新赋值才需要 global
cache: dict[str, object] = {}

def reset_cache():
    global cache     # ❗必须有！因为要改 cache 的指向（赋新值）
    cache = {}       # cache 现在是全新的空 dict
```

**判断口诀**：

| 操作 | 需要 `global`？ |
|------|:---:|
| 只读取全局变量 | ❌ 不需要 |
| 修改字典里某个 key 的值 | ❌ 不需要 |
| 往列表里 `append` | ❌ 不需要 |
| **重新赋值**全局变量（`=`） | ✅ 需要 |

---

## 3. Web 框架：FastAPI + uvicorn

### 3.1 FastAPI 是什么

[FastAPI](https://fastapi.tiangolo.com/) 是现代 Python Web 框架，特点：
- **异步原生**：基于 Starlette（ASGI）+ Pydantic
- **自动生成 OpenAPI 文档**：访问 `/docs` 即可看到 Swagger UI
- **WebSocket 内建支持**
- **类型驱动**：依赖 Pydantic 模型自动校验请求

### 3.2 创建应用和路由

```python
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

# 创建应用
app = FastAPI(
    title="Space AIAgent",
    description="航天分析平台智能助手",
    version="0.1.0",
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由（模块化）
from space_aiagent.api.routes import router as api_router
from space_aiagent.api.websocket import router as ws_router
app.include_router(api_router)
app.include_router(ws_router)
```

### 3.3 REST API 端点

```python
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/space", tags=["space"])


# POST 端点 — 用 Pydantic 模型定义请求/响应
class InvokeRequest(BaseModel):
    input: str = Field(description="用户输入")
    thread_id: str = Field(description="会话ID")

class InvokeResponse(BaseModel):
    output: dict = Field(description="Agent 输出")
    thread_id: str = Field(description="会话ID")

@router.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    """同步调用 Agent"""
    # ... Agent 处理逻辑
    return InvokeResponse(output={"content": "result"}, thread_id=request.thread_id)


# GET 端点
@router.get("/health")
async def health_check() -> dict:
    """健康检查"""
    return {"status": "ok", "service": "space-aiagent"}
```

### 3.4 WebSocket 端点

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    # 1. 接受连接
    await websocket.accept()

    # 2. 收发消息
    try:
        while True:
            # 接收文本
            raw = await websocket.receive_text()
            data = json.loads(raw)

            # 发送 JSON
            await websocket.send_json({"type": "ai_message", "content": "hello"})

    except WebSocketDisconnect:
        logger.info("连接已断开")
```

### 3.5 uvicorn 启动

```python
import uvicorn

# 方式1：Python 代码启动
uvicorn.run(
    "space_aiagent.main:app",
    host="0.0.0.0",
    port=8028,
    reload=True,        # 热重载（开发环境）
)

# 方式2：命令行启动
# uvicorn space_aiagent.main:app --reload --host 0.0.0.0 --port 8028
```

**Java 对比**：

| FastAPI 概念 | Java (Spring Boot) 类比 |
|-------------|------------------------|
| `FastAPI()` | `@SpringBootApplication` |
| `APIRouter` | `@RestController` + `@RequestMapping` |
| `@router.post("/path")` | `@PostMapping("/path")` |
| `@router.get("/path")` | `@GetMapping("/path")` |
| `@router.websocket("/ws")` | `@ServerEndpoint("/ws")` |
| `Query/Path/Body` 参数 | `@RequestParam`/`@PathVariable`/`@RequestBody` |
| Pydantic `BaseModel` | DTO + `@Valid` 校验 |
| `CORSMiddleware` | `CorsFilter` |

---

## 4. 数据验证：Pydantic

### 4.1 Pydantic 是什么

[Pydantic](https://docs.pydantic.dev/) (v2) 是 Python 的数据验证库，类似 Java Bean Validation (`@NotNull`/`@Valid`)，但更强大。

### 4.2 BaseModel 基本用法

```python
from pydantic import BaseModel, Field

# 定义模型（类似 Java DTO + 校验注解）
class ScenarioConfig(BaseModel):
    """创建场景的参数"""
    name: str = Field(default="新建场景", description="场景名称")
    central_body: str = Field(default="Earth", description="中心天体")
    start_time: str | None = Field(default=None, description="开始时间（ISO 8601）")
    end_time: str | None = Field(default=None, description="结束时间（ISO 8601）")
    description: str | None = Field(default=None, description="场景描述")


class EntityPosition(BaseModel):
    """实体位置"""
    longitude: float = Field(description="经度")
    latitude: float = Field(description="纬度")
    height: float = Field(default=0, description="高度（米）")


# 嵌套模型
class EntityConfig(BaseModel):
    """创建实体的参数"""
    entity_type: EntityType = Field(description="实体类型")    # 枚举类型
    name: str = Field(description="实体名称")
    position: EntityPosition = Field(description="实体位置")    # 嵌套
    properties: dict | None = Field(default=None, description="扩展属性")


# 使用方式
config = ScenarioConfig(name="测试场景", central_body="Earth")
print(config.model_dump())         # 转 dict（v2 用 model_dump，v1 用 dict）
print(config.model_dump_json())    # 转 JSON 字符串
```

### 4.3 `model_dump()` — 序列化

```python
from pydantic import BaseModel, Field

class ToolCallMessage(BaseModel):
    type: str = "tool_call"
    thread_id: str
    tool_func: str = Field(description="工具函数名")
    tool_func_args: dict = Field(default_factory=dict)
    tool_call_id: str = Field(default="")

# 序列化为 dict（然后可通过 WebSocket 以 JSON 发送）
msg = ToolCallMessage(
    thread_id="abc-123",
    tool_func="createScenario",
    tool_func_args={"name": "test"},
    tool_call_id="uuid-xxx",
)
print(msg.model_dump())
# 输出: {'type': 'tool_call', 'thread_id': 'abc-123', 'tool_func': 'createScenario',
#        'tool_func_args': {'name': 'test'}, 'tool_call_id': 'uuid-xxx'}

# WebSocket 中直接发送
await websocket.send_json(msg.model_dump())
```

### 4.4 `BaseSettings` — 配置管理

见[第 5 章](#5-配置管理pydantic-settings--yaml--env)。

**Java 对比**：

| Pydantic 概念 | Java 类比 |
|--------------|----------|
| `BaseModel` | POJO / DTO |
| `Field(default=...)` | `@Builder.Default` / 字段默认值 |
| `Field(description=...)` | `@Schema(description=...)` (Swagger) |
| `model_dump()` | `ObjectMapper.writeValueAsString()` |
| `model_validate(data)` | `objectMapper.readValue(json, Class)` |
| `model_dump_json()` | `new Gson().toJson(obj)` |

---

## 5. 配置管理：pydantic-settings + YAML + .env

### 5.1 设计思路

本项目的配置管理分为三层：

```
.env（敏感信息：API Key）  →  YAML（业务配置：host/port/log）  →  Pydantic Settings（类型安全）
```

**原则**：
- **`.env`**：存放 API Key、数据库密码等**敏感信息**，不提交 Git
- **YAML**：存放**业务配置**，支持多环境覆盖（`application.yaml` → `dev.yaml`/`prod.yaml`）
- **Pydantic Settings**：提供**类型安全**的配置访问接口

### 5.2 YAML 配置文件

```yaml
# config/application.yaml — 基础配置，支持 ${VAR:default} 语法
server:
  host: "${SERVER_HOST:0.0.0.0}"
  port: "${SERVER_PORT:8028}"
  workers: 1
  cors_origins:
    - "*"

agent:
  max_iterations: 10
  temperature: 0.1
  streaming: true
```

```yaml
# config/dev.yaml — 环境覆盖配置（只写需要覆盖的项）
logging:
  level: DEBUG
  format: console     # 开发环境用可读格式
  file:
    enabled: false    # 开发环境不写文件
```

```yaml
# config/subagents.yaml — 子 Agent 声明式配置
agents:
  - name: scene-agent
    description: 处理场景相关操作
    skills:
      - scene_management
    prompt_file: scene_agent.md

  - name: entity-agent
    description: 处理实体和轨道相关操作
    skills:
      - entity_management
      - orbit_management
    prompt_file: entity_agent.md
```

### 5.3 pydantic-settings 类型化配置

```python
from pydantic import Field
from pydantic_settings import BaseSettings

# 1. 定义配置子类
class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8028
    workers: int = 1
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

class LLMConfig(BaseSettings):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.1
    streaming: bool = True

class LoggingConfig(BaseSettings):
    level: str = "INFO"
    format: str = "json"
    console: bool = True
    file_enabled: bool = True
    file_dir: str = "./logs"
    file_max_bytes: int = 10 * 1024 * 1024    # 10MB
    file_backup_count: int = 10

# 2. 定义全局 Settings（聚合子配置）
class Settings(BaseSettings):
    app_name: str = "space-aiagent"
    app_version: str = "0.1.0"
    app_env: str = "dev"
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


# 3. 单例访问
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")          # 1. 加载 .env

        env = os.getenv("APP_ENV", "dev")
        yaml_config = _load_yaml_config(env)         # 2. 加载 YAML + 环境覆盖

        _settings = _apply_yaml_to_settings(yaml_config)  # 3. 映射到 Settings
    return _settings


# 4. 使用
settings = get_settings()
print(settings.server.host)      # "0.0.0.0"
print(settings.llm.model)        # "deepseek-chat"
print(settings.logging.level)    # "DEBUG" (dev 环境覆盖后)
```

### 5.4 `${VAR:default}` 环境变量解析

```python
import re

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

def _resolve_env_vars(value):
    """递归解析 YAML 中的 ${VAR:default} 语法"""
    if isinstance(value, str):
        def _replacer(match):
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(var_name, default)
        return _ENV_VAR_PATTERN.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value

# 示例:
# YAML: host: "${SERVER_HOST:0.0.0.0}"
# 如果 os.environ["SERVER_HOST"] = "192.168.1.1" → 返回 "192.168.1.1"
# 如果环境变量不存在 → 返回默认值 "0.0.0.0"
```

**Java 对比**：

| Python 方式 | Spring Boot 方式 |
|------------|-----------------|
| `pydantic-settings` | `@ConfigurationProperties` |
| `YAML + .env` | `application.yml + application-{env}.yml` |
| `${VAR:default}` | `${VAR:default}` (语法一致!) |
| `.env` + `python-dotenv` | `.env` + dotenv-java |

---

## 6. 结构化日志：structlog

### 6.1 structlog 是什么

[structlog](https://www.structlog.org/en/stable/) 是 Python 的结构化日志库，可以输出 JSON 格式日志，方便接入 ELK / Loki 等日志系统。它还**包装了标准库 `logging`**，所以已有的 `logging.getLogger()` 用法不用改。

### 6.2 日志初始化

```python
import logging
import logging.handlers
import structlog
from pathlib import Path


def setup_logging(level: str = "INFO", fmt: str = "console", ...):
    """一次性初始化日志系统"""

    # 1. 选择渲染器
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=True)    # 开发环境：彩色可读
    else:
        renderer = structlog.processors.JSONRenderer()          # 生产环境：JSON

    # 2. 配置处理链（processor chain）
    shared_processors = [
        structlog.contextvars.merge_contextvars,    # 合并上下文变量
        structlog.stdlib.add_log_level,             # 添加 level 字段
        structlog.stdlib.add_logger_name,           # 添加 logger 名
        _add_caller_info,                           # 自定义：添加文件名:行号
        structlog.processors.TimeStamper(fmt="iso"),# 添加时间戳
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,        # 格式化异常堆栈
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 3. 配置根 logger 的 handler
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    # 控制台 handler
    if console:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    # 文件 handler（带轮转）
    if file_enabled:
        log_path = Path(file_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / "space-aiagent.log",
            maxBytes=file_max_bytes,      # 10MB 自动轮转
            backupCount=file_backup_count, # 保留 10 个备份
            encoding="utf-8",
        )
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

### 6.3 使用方式

```python
# 在任何模块中
import logging

logger = logging.getLogger(__name__)

# 结构化日志：键值对参数
logger.info("场景创建成功", scene_name="test", thread_id="abc-123")
logger.warning("tool_call 超时: %s, id=%s", "createScenario", tool_call_id)
logger.exception("Agent 执行出错")

# 控制台输出（开发环境，彩色可读）：
# 2024-01-01 10:00:00 [info] 场景创建成功 scene_name=test thread_id=abc-123

# JSON 输出（生产环境）：
# {"timestamp": "2024-01-01T10:00:00", "level": "info", "event": "场景创建成功",
#  "scene_name": "test", "thread_id": "abc-123", "caller": "websocket.py:121"}
```

**Java 对比**：

| structlog | Java (SLF4J + Logback) |
|-----------|------------------------|
| `structlog.get_logger()` | `LoggerFactory.getLogger()` |
| `logger.info("msg", key=val)` | `logger.info("msg key={}", val)` (MDC) |
| `structlog.dev.ConsoleRenderer` | PatternLayout |
| `structlog.processors.JSONRenderer` | JsonLayout / logstash-logback-encoder |
| `RotatingFileHandler` | `RollingFileAppender` |

---

## 7. LLM 接入：langchain-openai

### 7.1 langchain-openai 是什么

`langchain-openai` 提供两种能力：
- **标准 OpenAI 接口**：`ChatOpenAI(model="gpt-4")` 调用 OpenAI
- **兼容接口**：任何实现了 OpenAI 兼容 API 的服务（DeepSeek、Qwen、vLLM 等）都可以用这个统一接入

本项目就是利用兼容接口同时支持 **DeepSeek** 和 **阿里 Qwen (DashScope)**。

### 7.2 构建 ChatOpenAI 实例

```python
from langchain_openai import ChatOpenAI
from space_aiagent.infrastructure.config import get_settings


def build_model() -> ChatOpenAI:
    """构建 LLM 实例（全局共享）"""
    settings = get_settings()
    llm = settings.llm

    return ChatOpenAI(
        model=llm.model,                  # "deepseek-chat" 或 "qwen-plus"
        openai_api_key=llm.api_key,       # API Key（从 .env 读取）
        openai_api_base=llm.base_url,     # 兼容接口的 Base URL
        temperature=llm.temperature,      # 0.1 — 低温度，更确定性
        streaming=llm.streaming,          # True — 流式输出
    )
```

### 7.3 多厂商切换

只需修改 `.env` 中的三个环境变量：

```bash
# DeepSeek
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 阿里 Qwen
# LLM_API_KEY=sk-xxxx
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=qwen-plus

# OpenAI 官方
# LLM_API_KEY=sk-xxxx
# LLM_BASE_URL=https://api.openai.com/v1
# LLM_MODEL=gpt-4
```

> **关键设计**：只需改环境变量，**零代码改动**。这是 OpenAI 兼容接口的最大价值。

### 7.4 `@tool` 装饰器

```python
from langchain_core.tools import tool

@tool
async def my_tool(param1: str, param2: int = 0) -> str:
    """工具的 docstring 会被 LLM 看到，所以要写清楚用途。

    参数:
        param1: 第一个参数的说明
        param2: 第二个参数的说明
    """
    # 工具逻辑
    return f"结果: {param1}_{param2}"

# 配合 Pydantic 模型做参数校验
@tool(args_schema=ScenarioConfig)
async def create_scenario(
    name: str = "新建场景",
    central_body: str = "Earth",
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
) -> dict:
    """创建航天场景。场景是所有实体的容器。"""
    # ... 工具逻辑
```

`@tool` 做了什么：
1. 将**普通函数包装成 LangChain 工具**，带有 name、description、args_schema
2. LLM 根据 tool 的 **name + description + 参数类型** 决定是否调用
3. docstring 越详细，LLM 调用越准确

---

## 8. Agent 框架：deepagents + langgraph

### 8.1 deepagents 是什么

[deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 是 LangChain 团队开发的 Agent Harness，提供：

- `create_deep_agent()`：一键创建主控 Agent
- **子 Agent 调度**：主控 Agent 可根据意图委派任务给子 Agent
- **FilesystemBackend + memory**：支持从知识文件加载 long-term memory

本项目用它充当 **Orchestrator（主控 Agent）**。

### 8.2 创建主控 Agent

```python
from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.types import Checkpointer


_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def create_orchestrator(
    subagents: list[dict],
    skill_loader: SkillLoader,
    checkpointer: Checkpointer,      # LangGraph checkpointer（持久化会话状态）
):
    """创建主控 Agent"""
    # 1. 构建系统提示词（注入 Skill 摘要）
    system_prompt = _build_system_prompt(registry)

    # 2. 构建 LLM 模型（通过兼容接口）
    model = build_model()

    # 3. 知识文件：通过 FilesystemBackend + memory 加载
    backend = FilesystemBackend(
        root_dir=str(_KNOWLEDGE_DIR),
        virtual_mode=True,              # 虚拟文件系统，不实际创建文件
    )

    # 4. 创建 Agent（传入 checkpointer 实现跨轮次会话记忆）
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,    # 系统提示词
        subagents=subagents,            # 子 Agent 列表
        backend=backend,                # 知识后端
        memory=["AGENTS.md"],           # 要加载的知识文件
        checkpointer=checkpointer,      # SQLite 持久化 checkpointer
    )
    return agent
```

### 8.3 子 Agent 加载

```python
# 从 YAML 配置加载子 Agent
import yaml
from pathlib import Path


def load_subagents(skill_loader: SkillLoader) -> list[dict]:
    """从 YAML 配置加载所有子 Agent"""
    config_text = _SUBAGENTS_CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)

    model = build_model()
    subagents: list[dict] = []

    for agent_cfg in config["agents"]:
        # 加载该 Agent 需要的工具（Skill）
        tools = skill_loader.load_skills(agent_cfg["skills"])

        # 读取提示词文件
        prompt = (_PROMPTS_DIR / agent_cfg["prompt_file"]).read_text(encoding="utf-8")

        # 构建子 Agent 配置
        subagents.append({
            "name": agent_cfg["name"],            # "scene-agent"
            "description": agent_cfg["description"],
            "model": model,                       # 复用 LLM 实例
            "tools": tools,                       # 已加载的工具列表
            "system_prompt": prompt,              # 提示词
        })

    return subagents
```

对应的 YAML 配置：

```yaml
agents:
  - name: scene-agent
    description: 处理场景相关操作
    skills:
      - scene_management
    prompt_file: scene_agent.md
```

### 8.4 调用 Agent

```python
from langchain_core.messages import HumanMessage

# 获取 checkpointer（从数据库模块获取 AsyncSqliteSaver）
from space_aiagent.infrastructure.database import get_db
db = await get_db()
checkpointer = await db.get_checkpointer()

# 创建 Agent（传入 checkpointer 实现会话持久化）
agent = create_orchestrator(subagents, skill_loader, checkpointer)

# 执行
result = await agent.ainvoke(              # ainvoke = async invoke
    {"messages": [HumanMessage(content="帮我创建一个场景")]},
    config={"configurable": {"thread_id": "abc-123"}},
)

# 提取最终 AI 回复
messages = result.get("messages", [])
for msg in reversed(messages):
    if hasattr(msg, "content") and msg.type == "ai":
        final_reply = msg.content
        break
```

**Java 对比（概念层面）**：

| deepagents 概念 | 类比 |
|----------------|------|
| `create_deep_agent()` | 创建主 Controller / Router |
| `subagents` | 模块化的子 Service |
| `tools` | 可调用的工具方法 |
| `system_prompt` | Agent 的行为指令 |
| `memory` | 长期记忆 / 知识库 |
| Orchestrator → 子 Agent | 类似路由分发模式 |

---

## 9. Agent 调用模式：invoke vs stream vs astream_events

LangGraph 的 `CompiledStateGraph` 提供了多种调用方式，每种适用于不同的场景。理解它们的区别对于调试和生产部署至关重要。

### 9.1 四种调用方式

```python
from langchain_core.messages import HumanMessage

config = {
    "configurable": {"thread_id": "abc-123"},
    "recursion_limit": 100,
}
user_input = {"messages": [HumanMessage(content="创建场景")]}

# 1. ainvoke — 异步调用，等全部完成后返回最终状态
result = await agent.ainvoke(user_input, config=config)

# 2. invoke — 同步调用，同上但阻塞当前线程
result = agent.invoke(user_input, config=config)

# 3. astream — 异步流式，逐 chunk 返回状态更新
async for chunk in agent.astream(user_input, config=config):
    # chunk 是部分状态（如 {messages: [AIMessageChunk(...)]}）
    print(chunk)

# 4. astream_events — 最细粒度的事件流，可监听每个中间步骤
async for event in agent.astream_events(
    user_input,
    config=config,
    version="v2",   # v1/v2 走 langchain_core 基类；v3 走 LangGraph 原生
):
    kind = event["event"]       # on_tool_start / on_chat_model_start / ...
    name = event.get("name")    # 工具名或节点名
    data = event.get("data")    # 输入/输出数据
```

**选择指南**：

| 场景 | 推荐方式 | 原因 |
|------|---------|------|
| 简单 API / 后台批处理 | `ainvoke` | 拿到最终结果即可，不需要流 |
| 实时聊天 UI（逐字显示） | `astream` | 支持 token 级别流式输出 |
| 需要监听工具调用、LLM 决策、AgentResponse | `astream_events` (v2) | 最细粒度，能捕获中间事件 |
| 需要 Tracing + 工具包装器 | `astream_events` (v3) | 走 LangGraph 原生路径，支持 config 合并 |

### 9.2 config 关键参数

```python
config: RunnableConfig = {
    # 1. thread_id — 会话标识（必须）
    # LangGraph 用它做 checkpoint 持久化，同一个 thread_id 共享消息历史
    "configurable": {"thread_id": "plugin_sceneAgent_123"},

    # 2. recursion_limit — 图执行步数上限（重要！）
    # 默认值取决于调用路径（见 9.3 节）
    "recursion_limit": 100,

    # 3. metadata — 自定义元数据（可选）
    "metadata": {"user_id": "u-123", "source": "web"},

    # 4. tags — 标签（可选，LangSmith tracing 用）
    "tags": ["production", "scene-create"],

    # 5. max_concurrency — 并发控制（可选）
    "max_concurrency": 5,
}
```

**`configurable` 中常用的键**：

| 键 | 用途 |
|----|------|
| `thread_id` | 会话 ID，区分不同用户/对话 |
| `checkpoint_ns` | checkpoint 命名空间（多 Agent 场景隔离状态） |
| `checkpoint_id` | 从指定 checkpoint 恢复（回放历史） |

### 9.3 recursion_limit 深度解析

这是本项目中踩过的最隐蔽的坑，专门展开讲解。

#### 9.3.1 什么是 recursion_limit

LangGraph 的 Agent 执行本质是**图遍历**：模型节点 → 工具节点 → 模型节点 → ... 形成一个循环。`recursion_limit` 限制这个循环的最大步数，防止 Agent 无限运行。

```
                    ┌──────────────┐
        ┌──────────►│  model (LLM) │◄─────────┐
        │           └──────┬───────┘          │
        │                  │                   │
        │           ┌──────▼───────┐          │
        │           │  tools       │          │
        │           └──────┬───────┘          │
        │                  │                   │
        └──────────────────┴───────────────────┘
             每一步消耗 1 个 recursion 配额
```

每一步 = 一个 graph node 的执行。如果 Agent 在 25 步内没有到达终止状态（END），就抛出 `GraphRecursionError`。

#### 9.3.2 本项目的 Bug：为什么 ainvoke 不出问题，astream_events(v2) 却报 25 步限制？

**两条调用路径走了不同的 `ensure_config`**：

```
ainvoke(path)        → Pregel.ainvoke() → LangGraph ensure_config  → 默认 recursion_limit = 10007
astream_events(v2)   → Runnable.astream_events() → langchain_core ensure_config → 默认 recursion_limit = 25
```

`version="v2"` 走的是 `langchain_core.runnables.base.Runnable.astream_events()`，这是基类实现，不经过 LangGraph 的配置系统。它用 `langchain_core` 自带的 `ensure_config`，默认 `recursion_limit = 25`。

而 deepagents 框架通过 `.with_config({"recursion_limit": 9_999})` 设置的 graph 级别默认值，在 `astream_events(v2)` 路径上**被 langchain_core 的 25 覆盖了**。

**修复方式**：在 config 中显式指定 `recursion_limit`，不依赖框架默认值合并：

```python
async for event in agent.astream_events(
    {"messages": [HumanMessage(content=user_msg.content)]},
    config={
        "configurable": {"thread_id": user_msg.thread_id},
        "recursion_limit": 100,   # ← 显式指定，不依赖 graph 级别的继承
    },
    version="v2",
):
```

**一句话总结**：`astream_events(v2)` 的 config 合并绕过了 LangGraph 的 `ensure_config`（默认 10007），用了 langchain_core 的版本（默认 25）。永远在 config 中**显式指定 `recursion_limit`**，不要依赖框架默认值。

#### 9.3.3 recursion_limit 应该设多少？

| 场景 | 建议值 | 说明 |
|------|--------|------|
| 简单查询（无工具调用） | 10-25 | 1 次 LLM 调用就返回 |
| 单步工具调用 | 25-50 | 1-2 次 tool call + LLM 往返 |
| 多步工具调用 + 子 Agent | 50-100 | 含 task 委派和子 Agent 内部步骤 |
| 复杂编排（多子 Agent 协作） | 100-200 | 含多次 task 委派 |
| 作为安全网（生产环境） | 100 | 覆盖正常需求，异常时及时中断 |

本项目设置 `recursion_limit = 100`：Orchestrator 委派子 Agent → 子 Agent 执行 1-3 个工具 → 返回 → Orchestrator 汇总，正常约 5-15 步，100 留了充分余量。

### 9.4 四种方式的实现差异

```
ainvoke(input, config)
  └─ Pregel.ainvoke()
       └─ self.astream(input, config, stream_mode="values")  ← 消费所有 chunk
            └─ 返回最终状态

astream(input, config)
  └─ Pregel.astream()           ← 使用 LangGraph 的 ensure_config (默认 10007)
       └─ 逐 chunk 产出状态更新

astream_events(input, config, version="v2")
  └─ Runnable.astream_events()  ← 使用 langchain_core 的 ensure_config (默认 25)
       └─ 通过回调机制生成 StreamEvent 字典
       └─ 事件类型：on_chain_start / on_chat_model_start / on_tool_start / ...

astream_events(input, config, version="v3")
  └─ Pregel._apregel_stream_v3() ← 使用 LangGraph 的 ensure_config (默认 10007)
       └─ 再内部调用 self.astream()
```

### 9.5 astream_events 事件体系深度解析

本节回答一个项目里真实踩过的坑：**为什么 `on_chat_model_end` 事件里的 `AIMessage.content` 是空字符串，明明中间件已经写了渲染文本？** 要讲清楚这个问题，必须理解 astream_events 事件体系与 AgentMiddleware 钩子的时序关系。

#### 9.5.1 事件按 Runnable 调用栈分层

LangChain 里所有事物都是 `Runnable`：ChatModel 是 Runnable，Tool 是 Runnable，整个 Agent 图也是 Runnable。`astream_events` 本质是把整个调用栈里**每个 Runnable 的开始/结束/流式 chunk**都广播出来。

事件命名遵循 `on_<组件类型>_<动作>` 模式：

| 事件 | 触发时机 | `data` 字段 |
|------|---------|------------|
| `on_chain_start` | 任何 Runnable 开始 | `{"input": ...}` |
| `on_chain_end` | 任何 Runnable 结束 | `{"output": ...}` |
| `on_chain_stream` | Runnable 流式产出 chunk | `{"chunk": ...}` |
| `on_chat_model_start` | LLM 调用前 | `{"input": messages, "prompts": [...]}` |
| `on_chat_model_stream` | LLM 每个 token | `{"chunk": AIMessageChunk}` |
| `on_chat_model_end` | LLM 调用结束 | `{"output": AIMessage, "input": ...}` |
| `on_tool_start` | 工具调用前 | `{"input": tool_args_dict}` |
| `on_tool_end` | 工具调用结束 | `{"output": ToolMessage}` |
| `on_text` | 任意文本输出（如 prompt 渲染） | `{"text": str}` |
| `on_retriever_start` / `on_retriever_end` | 检索器调用 | 类比上面 |

每个事件还携带这些元字段（不在 `data` 里）：

```python
{
    "event": "on_tool_end",
    "name": "createScenario",          # 组件名
    "data": {...},                      # 上表所列
    "run_id": "uuid-xxx",              # 本次执行的唯一 ID
    "tags": ["production"],            # Runnable tagging
    "metadata": {"langgraph_node": "tools"},
    "parent_ids": ["uuid-parent-xxx"], # 调用栈父级 ID（嵌套追踪）
}
```

#### 9.5.2 Agent 图里的事件流（实战 dump）

用 `astream_events(version="v2")` 跑 deepagents 的 orchestrator 委派 scene-agent 创建场景，事件流大致长这样（简化版）：

```
on_chain_start  name="LangGraph"              # 顶层图启动
on_chain_start  name="agent"                  # orchestrator 的 model node 启动
  on_chat_model_start name="ChatOpenAI"       # LLM 调用前
    on_chat_model_stream × N                  # token 流（被 LLM 输出风格决定）
  on_chat_model_end   name="ChatOpenAI"       # LLM 调用结束（输出原始 AIMessage）
on_chain_end    name="agent"                  # model node 完成
on_chain_start  name="tools"                  # tools node 启动（执行 task 工具）
  on_tool_start name="task"                   # task 工具启动（task 内部委派子 agent）
    on_chain_start  name="LangGraph"          # 子 agent 图启动（subgraph）
    ... 子 agent 内部的事件流 ...
    on_chain_end    name="LangGraph"          # 子 agent 图完成
  on_tool_end   name="task"                   # task 工具完成（输出 ToolMessage）
on_chain_end    name="tools"                  # tools node 完成
on_chain_start  name="agent"                  # orchestrator 再次进 model node（基于工具结果生成最终回复）
  on_chat_model_start ...
  on_chat_model_end   ...
on_chain_end    name="agent"                  # 最终 model node 完成（输出含 AgentResponse 的 AIMessage）
on_chain_end    name="LangGraph"              # 顶层图完成
```

观察关键点：
- **每个 node 完成时都发射 `on_chain_end`** —— name 区分是哪个 node
- **`on_chat_model_end` 在 `on_chain_end`（agent node）内部触发** —— 早于 agent node 完成
- **子图（subagent）是嵌套的 `LangGraph`** —— 自己有完整的 chain/chat_model/tool 事件流

#### 9.5.3 中间件（AgentMiddleware）介入时机：核心陷阱

deepagents/LangGraph 的 `AgentMiddleware` 提供几个钩子包装 LLM 调用和工具调用：

| 钩子 | 作用 |
|------|------|
| `awrap_model_call(request, handler)` | 包装整个 model node 内部的 LLM 调用，可改输入/输出 |
| `awrap_tool_call(request, handler)` | 包装工具调用，可改输入/输出 |
| `a_before_model(state, runtime)` | model 调用**前**注入命令（如短路） |
| `a_after_model(state, runtime)` | model 调用**后**修改 state |
| `amodify_model_request(request)` | 改 LLM 请求（消息列表、tools） |

本项目 `ResponseStabilizationMiddleware` 用 `awrap_model_call`：

```python
class ResponseStabilizationMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        # 1. 调 handler 内部触发 on_chat_model_start/stream/end 事件
        response = await handler(request)
        # 2. 后置处理：把渲染文本写回 AIMessage.content
        return self._stabilize(response)
```

`handler` 内部的调用链：`handler(request)` → `_execute_model_async` → `model.ainvoke(messages)` → 触发 `on_chat_model_*` 事件 → 返回 `ModelResponse`。

**关键陷阱**：`on_chat_model_end` 事件由 `model.ainvoke()` 自己发射，**时机早于 handler 返回**。也就是说：

```
awrap_model_call 调用栈
├─ 前置处理
├─ await handler(request)
│   ├─ model.ainvoke(messages)
│   │   ├─ on_chat_model_start        ← 事件
│   │   ├─ on_chat_model_stream × N   ← 事件
│   │   └─ on_chat_model_end          ← 事件 (此时输出原始 AIMessage，content="")
│   └─ 返回 ModelResponse（原始）
├─ 后置处理：self._stabilize(response)  ← 这里才把 content 改成渲染文本
└─ 返回改后的 ModelResponse
```

所以**从 `on_chat_model_end` 事件里读到的 `output.content` 永远是 LLM 原始输出**（空字符串或 LLM 自带的文本），中间件写回的渲染文本进不去这个事件。中间件改后的 AIMessage 只会进 LangGraph state，由后续的 `on_chain_end` 事件携带。

#### 9.5.4 哪些事件能拿到中间件改后的消息？

| 事件 | 拿得到中间件改后的 AIMessage 吗 | 原因 |
|------|----------------------------|------|
| `on_chat_model_start` / `on_chat_model_stream` | ❌ | LLM 调用前/中，中间件还没运行 |
| `on_chat_model_end` | ❌ | LLM 调用刚结束，中间件后置处理还没运行 |
| `on_tool_start` / `on_tool_end`（业务工具） | ✅（针对 `awrap_tool_call`） | 同样的时序原理，但工具中间件的后置处理也早于 on_tool_end？**实际不一定**——见下表 |
| `on_chain_end`（model node） | ✅ | node 整体完成才发射，中间件后置处理已结束，state 已含改后的消息 |
| `on_chain_end`（顶层 graph） | ✅ | 整个图跑完，state 是最终状态 |

> ⚠️ **关于 on_tool_end**：项目早期版本曾监听 `on_tool_end` 拿 `AgentResponse`（ToolStrategy 把结构化输出当工具调用），但实际验证收不到——因为 deepagents 把 `ToolStrategy(AgentResponse)` 在 model node 内部直接拦截转成结构化响应，**不会走 ToolsNode**，自然没有 `on_tool_end`。代码注释里也明确写了这一点。

#### 9.5.5 实战：用 on_chain_end 提取 AgentResponse 含渲染文本

项目 `src/space_aiagent/api/websocket.py` 的最终实现：

```python
async for event in agent.astream_events(
    {"messages": [HumanMessage(content=user_msg.content)]},
    config={"configurable": {"thread_id": ...}, "recursion_limit": 100},
    version="v2",
):
    kind = event["event"]
    name = event.get("name", "")
    data = event.get("data", {})

    if kind == "on_tool_start":
        # 1. 工具进度提示 + task 死循环兜底
        if name == "task":
            task_call_count += 1
            if task_call_count >= LOOP_THRESHOLD:
                await bridge.send_ai_message("...")
                await bridge.send_end()
                return
        # ...

    elif kind == "on_chain_end":
        # 2. model node 完成，AIMessage.content 已被中间件写入渲染文本
        output = data.get("output")
        if not isinstance(output, dict):
            continue
        for msg in output.get("messages", []):
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                continue
            agent_response_tc = next(
                (tc for tc in msg.tool_calls if tc.get("name") == "AgentResponse"),
                None,
            )
            if agent_response_tc is None:
                continue
            # msg.content 是 ResponseStabilizationMiddleware._stabilize 写的渲染文本
            # 不需要出口处再 render 一次
            await bridge.send_ai_message(msg.content)
            await bridge.send_end()
            return
```

**为什么不在循环出口处再 render 一次？** 因为同一条 AIMessage 已经被中间件 render 过：
- 中间件 `_stabilize` 调 `ResponseRenderer().render()` → 写入 `AIMessage.content`
- `AIMessage` 进 state.messages → 被 checkpointer 持久化（供下一轮 LLM 看见 cross-turn 上下文）
- `on_chain_end` 事件携带 state delta，里面的 `AIMessage.content` 就是渲染文本

websocket 在 `on_chain_end` 里读 `msg.content` 直接发前端即可，不需要重复 render。两次 render 必然产生相同结果（renderer 是纯函数），所以保留一次就够。

### 9.6 astream(stream_mode=...) 体系

`astream` 是 LangGraph 原生的流式 API，比 `astream_events` 更底层。它通过 `stream_mode` 参数控制 chunk 的形状。

#### 9.6.1 五种 stream_mode 速查

| stream_mode | chunk 内容 | 典型用途 |
|------------|-----------|---------|
| `"values"` | 每步后**完整 state 快照** | 看每步后状态全貌（消息历史越长越大） |
| `"updates"` | 每步的 **state 增量** `{node_name: delta}` | 看每步改了什么（最常用） |
| `"messages"` | `(AIMessageChunk, metadata)` 元组 | token 级流式，前端打字机效果 |
| `"custom"` | 节点内 `writer(...)` 自定义数据 | 节点内部往外抛自定义事件 |
| `"debug"` | 调试信息（task/state/object） | 验证图结构 |

五种 mode 可以**单选**也可以**组合**（列表传参）。

#### 9.6.2 updates 模式 chunk 格式

```python
async for chunk in agent.astream(
    {"messages": [HumanMessage(content="创建场景")]},
    config={"configurable": {"thread_id": "abc"}, "recursion_limit": 100},
    stream_mode="updates",
):
    print(chunk)
```

输出（简化）：

```python
# orchestrator 的 model node 完成
{
    "agent": {
        "messages": [
            AIMessage(content="", tool_calls=[{"name": "task", "args": {...}}])
        ]
    }
}

# orchestrator 的 tools node 完成（执行了 task）
{
    "tools": {
        "messages": [
            ToolMessage(content="场景创建成功", tool_call_id="call_xxx")
        ]
    }
}

# orchestrator 再次进 model node，输出最终 AgentResponse
{
    "agent": {
        "messages": [
            AIMessage(
                content="场景「新建场景」已创建成功！...",  # ← 中间件 _stabilize 已写入
                tool_calls=[{"name": "AgentResponse", "args": {...}}]
            )
        ]
    }
}
```

每个 chunk 都是 `{node_name: state_delta}`。**这里的 `AIMessage.content` 是中间件 `_stabilize` 写入后的渲染文本**——因为 chunk 是 node 完成后才发射，中间件后置处理早已执行。

#### 9.6.3 subgraphs=True：子图（subagent）流式

默认 `astream` 只收**顶层图**的 chunk。subagent 是 subgraph，要在 chunk 里看到它，必须 `subgraphs=True`：

```python
async for chunk in agent.astream(
    input,
    config=config,
    stream_mode="updates",
    subgraphs=True,  # ← 启用子图流
):
    # chunk 现在是 (namespace_tuple, mode, data) 三元组
    ns, mode, data = chunk
    print(ns, mode, data)
```

输出示例：

```python
# 顶层 orchestrator 的 model node
((), "updates", {"agent": {"messages": [AIMessage(...)]}})

# 子 agent（scene-agent）启动后的事件
(("scene-agent:...",), "updates", {"agent": {"messages": [AIMessage(...)]}})
#  ↑ namespace 元组，空 () 表顶层，非空表子图层级（多级嵌套会变长）

# 子 agent 内部的工具调用
(("scene-agent:...",), "updates", {"tools": {"messages": [ToolMessage(...)]}})
```

namespace 元组的长度反映嵌套深度。`()` 是顶层，`("scene-agent:xxx",)` 是一层子图，`("scene-agent:xxx", "sub-sub:yyy",)` 是两层嵌套。

#### 9.6.4 多模式组合：stream_mode=[...]

实际场景常常需要同时拿多种数据。例如既要 token 流（前端打字机效果），又要 node 级 state delta（更新 UI）：

```python
async for chunk in agent.astream(
    input,
    config=config,
    stream_mode=["messages", "updates"],  # ← 列表传参
    subgraphs=True,
):
    # chunk 是 (namespace, mode, data) 三元组（多模式时永远三元组，即使 subgraphs=False）
    ns, mode, data = chunk

    if mode == "messages":
        msg_chunk, msg_meta = data
        # msg_chunk 是 AIMessageChunk，含增量 token
        # msg_meta 含 langgraph_node 等信息
        print(f"[token] {msg_chunk.content}", end="", flush=True)

    elif mode == "updates":
        for node, delta in data.items():
            print(f"[{node}] 更新了 {list(delta.keys())}")
```

**注意**：当 `stream_mode` 是列表时，即使 `subgraphs=False`，chunk 也是 `(namespace, mode, data)` 三元组。当 `stream_mode` 是单值时，chunk 就是 data 本身（无 namespace 包装），除非 `subgraphs=True`。

#### 9.6.5 custom 模式：节点内往外抛自定义事件

```python
from langgraph.config import get_stream_writer

async def my_node(state):
    writer = get_stream_writer()
    writer({"progress": "50%", "step": "正在查询"})  # ← 自定义数据
    # ... 业务逻辑 ...
    writer({"progress": "100%", "step": "完成"})
    return {"messages": [...]}

# 消费侧
async for chunk in agent.astream(input, stream_mode="custom"):
    print(chunk)  # {"progress": "50%", "step": "正在查询"}
```

适合做精细进度提示（比 `_make_progress_message` 这种基于 `on_tool_start` 的方案更灵活）。

### 9.7 on_chain_end（astream_events）vs updates（astream）选型

项目里真实讨论过的选型问题。结论：本项目选 `on_chain_end`，理由按重要度排：

| 维度 | `astream_events` + `on_chain_end` | `astream(stream_mode="updates")` |
|------|----------------------------------|----------------------------------|
| **API 互斥** | 保留所有 `on_*` 事件（tool/chat_model/retriever...） | 放弃 `astream_events` 整套事件协议 |
| **loop 检测时机** | `on_tool_start` 工具**开始前**计数，第 2 次立刻中断 | 只能从 ToolMessage 反推，**工具完成后**才计数（task subagent 可能跑几十秒） |
| **subgraph 区分** | `event["name"]` + `parent_ids` 字符串过滤 | `(namespace_tuple, mode, data)` 元组递归 |
| **改动量** | 1 处事件类型 + 数据提取，~15 行 | 整个流式循环重写，~50 行 + loop 检测重做 |
| **稳定性** | `astream_events` 是 Runnable 协议稳定接口 | `updates` chunk 格式在 LangGraph 0.x → 1.x 有过 breaking |
| **未来扩展** | token 流（`on_chat_model_stream`）、检索事件（`on_retriever_*`）、自定义进度（`on_tool_start`）天然支持 | 全部要重做或换 stream_mode 组合 |
| **payload 直观度** | `data.output.messages` 嵌套较深 | `{node: delta}` 扁平，state delta 更直接 |

**核心权衡**：updates 模式在"拿 state delta"这一点上确实更直接（不需要从 event 嵌套结构里挖 messages），但代价是放弃 astream_events 整个事件协议。对于本项目这种"需要 loop 检测 + 未来可能加 token 流"的场景，`on_chain_end` 是更高 ROI 的选择。

**决策树**：

```
你的应用需要监听哪些事件？
├─ 只关心最终结果 → ainvoke
├─ 关心每步 state 变化 + 节点身份清晰
│   ├─ 不需要细粒度事件（工具/LLM/检索）
│   │   └─ astream(stream_mode="updates")
│   └─ 需要细粒度事件 → astream_events + on_chain_end
├─ 需要 token 级打字机效果 → astream(stream_mode="messages")
└─ 需要多种组合 → astream(stream_mode=["messages", "updates", ...])
```

**项目实战结论**：`astream_events(version="v2")` + `on_tool_start`（loop 检测 + 进度钩子）+ `on_chain_end`（提取 AgentResponse 含渲染文本）。不用 updates 是因为 loop 检测的时机敏感（task 工具内部是 subagent run，几十秒延迟不可接受），且未来可能加 token 流式。

---

## 10. 工具定义：langchain_core.tools

### 10.1 `@tool` 装饰器详解

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field

# 基础用法：docstring 就是给 LLM 看的描述
@tool
async def rename_scenario(name: str) -> dict:
    """重命名当前场景。
    参数:
        name: 新的场景名称
    """
    bridge = bridge_var.get()
    result = await bridge.send_tool_call("renameScenario", args={"name": name})
    return result


# 进阶用法：用 args_schema 做参数校验
class ScenarioConfig(BaseModel):
    name: str = Field(default="新建场景", description="场景名称")
    central_body: str = Field(default="Earth", description="中心天体")

@tool(args_schema=ScenarioConfig)
async def create_scenario(
    name: str = "新建场景",
    central_body: str = "Earth",
) -> dict:
    """创建航天场景。场景是所有实体的容器，添加卫星前必须先创建场景。"""
    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        "createScenario",
        args={"name": name, "centralBody": central_body},
    )
    return result
```

### 10.2 `BaseTool` — 工具的类型标识

LangChain 用 `isinstance(obj, BaseTool)` 来判断一个属性是否是工具：

```python
from langchain_core.tools import BaseTool

def _extract_tools(self, module) -> list[BaseTool]:
    """从动态导入的模块中提取所有工具函数"""
    tools: list[BaseTool] = []
    for attr_name in dir(module):               # 遍历模块所有属性
        attr = getattr(module, attr_name)       # 获取属性值
        if isinstance(attr, BaseTool):          # 判断是否为工具
            tools.append(attr)
    return tools
```

---

## 11. 异步桥接：asyncio.Future + ContextVar

### 11.1 为什么需要桥接

本项目的工具函数**不直接执行操作**，而是通过 WebSocket 发送指令给前端 Cesium 执行。这意味着：

```
Agent 调用工具 → 发送指令到前端 → 等待前端执行 → 返回结果给 Agent
```

中间的"等待"用 `asyncio.Future` 实现。

### 11.2 WSBridge 实现

```python
import asyncio
import uuid
from fastapi import WebSocket

class WSBridge:
    """WebSocket 远程工具桥接"""

    def __init__(self, websocket: WebSocket, thread_id: str) -> None:
        self._ws = websocket           # WebSocket 连接
        self._thread_id = thread_id
        self._pending: dict[str, asyncio.Future] = {}   # tool_call_id → Future

    async def send_tool_call(self, tool_func: str, args: dict, timeout: float = 60) -> dict:
        """
        发送工具调用到前端，等待并返回执行结果

        步骤:
        1. 生成唯一 ID（tool_call_id）
        2. 创建 Future 并缓存
        3. 通过 WebSocket 发送 tool_call 消息
        4. await Future（等待前端返回结果）
        5. 返回结果
        """
        # 1. 创建 Future
        tool_call_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[tool_call_id] = future

        # 2. 发送指令到前端
        message = ToolCallMessage(
            thread_id=self._thread_id,
            tool_func=tool_func,
            tool_func_args=args,
            tool_call_id=tool_call_id,
        )
        await self._ws.send_json(message.model_dump())

        # 3. 等待前端返回结果（带超时）
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except TimeoutError:
            self._pending.pop(tool_call_id, None)
            return {"success": False, "message": f"工具调用超时: {tool_func}"}

    def resolve_tool_result(self, result: ToolResultMessage) -> None:
        """
        前端返回结果时调用，resolve 对应的 Future

        由 WebSocket handler 在收到 tool_result 消息时调用。
        """
        tool_call_id = result.tool_call_id
        future = self._pending.pop(tool_call_id, None)
        if future and not future.done():
            future.set_result({                    # resolve Future
                "success": result.success,
                "message": result.message,
                "data": result.data,
            })

    def cleanup(self) -> None:
        """
        清理所有未完成的 Future

        WebSocket 断开时调用。
        """
        for _, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("WebSocket 连接已断开"))
        self._pending.clear()
```

### 11.3 ContextVar 注入

```python
# bridge/__init__.py
from contextvars import ContextVar

# 会话级别变量：每个 WebSocket 连接对应一个 bridge
bridge_var: ContextVar[WSBridge | None] = ContextVar("bridge_var", default=None)
```

```python
# api/websocket.py — 在 WebSocket handler 中注入
bridge = session_manager.register(thread_id, websocket)
token = bridge_var.set(bridge)
try:
    result = await agent.ainvoke(...)
finally:
    bridge_var.reset(token)
```

```python
# skills/scene_management/tools.py — 在工具函数中获取
@tool
async def create_scenario(name: str = "新建场景") -> dict:
    bridge = bridge_var.get()
    if bridge is None:
        return {"success": False, "message": "bridge 未注入"}
    result = await bridge.send_tool_call("createScenario", args={"name": name})
    return result
```

### 11.4 完整时序图

```
工具函数(create_scenario)           WSBridge              WebSocket              前端
        │                              │                     │                    │
        ├── bridge_var.get() ──────────►│                     │                    │
        ├── send_tool_call() ──────────►│                     │                    │
        │                              ├── create_future()    │                    │
        │                              ├── send_json() ──────►│                    │
        │                              │                     ├── tool_call ──────►│
        │                              │                     │                    ├── 执行 Cesium 操作
        │                              │                     │  tool_result ◄────┤
        │                              │  resolve_tool ◄─────┤                    │
        │                              ├── future.set_result()                    │
        │  await future (返回) ◄───────┤                     │                    │
        │                              │                     │                    │
        ▼                              │                     │                    │
    返回结果给 Agent
```

**Java 类比**：

| Python | Java |
|--------|------|
| `asyncio.Future` | `CompletableFuture<T>` |
| `future.set_result(val)` | `future.complete(val)` |
| `asyncio.wait_for(future, timeout)` | `future.get(timeout, unit)` |
| `ContextVar` | `ThreadLocal<T>`（但支持协程传播） |

### 11.5 WebSocket 死锁 Bug 详解

这是本项目中踩过的一个**经典的 asyncio 并发陷阱**，值得专门拿出来讲解。

#### 11.5.1 Bug 现象

在最初版本的 `websocket.py` 中，Agent 执行是**直接放在消息接收循环中**的：

```python
# ❌ 原始实现（有 bug）
@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    while True:
        raw = await websocket.receive_text()       # ① 等待前端消息
        data = json.loads(raw)

        if data["type"] == "user_input":
            # ❌ 直接在当前协程中执行 Agent！
            result = await agent.ainvoke(...)      # ② Agent 执行（可能需要工具调用）
            await ws.send_json(ai_response)        # ③ 发送回复
```

表面上看这很合理：收到消息 → 执行 Agent → 回复。但实际上这段代码**会死锁**。

#### 11.5.2 死锁原理

问题的根本原因是：**Agent 的工具调用需要"前端返回 tool_result"，但前端返回 tool_result 需要通过 WebSocket 被接收到，而 WebSocket 的接收循环正在 `await agent.ainvoke(...)` 处被阻塞！**

```
死锁示意图：

时间轴 →

协程（唯一的消息处理协程）
│
├─ await receive_text()          ← 收到 user_input，继续执行
├─ await agent.ainvoke(...)      ← Agent 开始执行
│   └─ Agent 调用 createScenario 工具
│       └─ bridge.send_tool_call()
│           ├─ send_json(tool_call)   ← 发送 tool_call 到前端 ✅
│           └─ await future           ← 🔴 等待前端返回 tool_result
│                                       （协程在此挂起）
│
│   ...此时前端执行完 Cesium 操作，发送 tool_result 到 WebSocket...
│   ...但没有人调用 receive_text() 来接收！...
│   ...因为唯一的协程正在 await future 处等待！...
│
│   🔴 死锁！Agent 等前端结果 → 前端结果已发送 → 没人接收 → 永远等下去
│
└─ 超时（60秒后）→ 返回超时错误
```

**核心矛盾**：同一个协程既要"接收 WebSocket 消息"，又要"等待 Agent 工具调用的返回结果"，但这两个动作都需要同一个协程来执行，形成了"自己等自己"的死锁。

#### 11.5.3 为什么会发生

问题的本质是 **WebSocket 连接上有两种不同的消息流需要并发处理**：

| 消息流 | 方向 | 触发时机 |
|--------|------|---------|
| 用户输入 | 前端→后端 | 用户发送新消息（新轮次开始） |
| 工具结果 | 前端→后端 | 前端执行完 Cesium 操作（Agent 等待中） |

Agent 在执行过程中发出 tool_call 后，必须等待前端返回 tool_result 才能继续。但 tool_result 需要被 `receive_text()` 接收到才能处理。如果接收循环被 Agent 执行阻塞，tool_result 就永远收不到。

#### 11.5.4 正确解法：`asyncio.create_task` 后台执行

**核心思路**：将 Agent 执行放到独立的 `asyncio.Task` 中，让主循环专职接收消息。

```python
# ✅ 正确实现（当前版本）
@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    agent_tasks: set[asyncio.Task] = set()     # 追踪后台任务

    # 后台 Agent 执行器（独立的协程）
    async def run_agent(bridge, token, user_msg) -> None:
        try:
            agent = await _get_or_create_agent(user_msg.thread_id)

            # Agent 在后台任务中执行，不阻塞消息接收循环
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_msg.content)]},
                config={"configurable": {"thread_id": user_msg.thread_id}},
            )

            # 提取 AI 回复并发送
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.type == "ai":
                    await bridge.send_ai_message(msg.content)
                    break
            await bridge.send_end()

        except Exception as e:
            await bridge.send_error(str(e))
        finally:
            bridge_var.reset(token)

    try:
        while True:
            raw = await websocket.receive_text()     # ① 只负责接收消息
            data = json.loads(raw)

            if data["type"] == "user_input":
                user_msg = UserInputMessage(**data)
                bridge = session_manager.register(user_msg.thread_id, websocket)
                token = bridge_var.set(bridge)

                # ② Agent 在后台 Task 中执行，主循环立即回到 receive_text()
                task = asyncio.create_task(run_agent(bridge, token, user_msg))
                agent_tasks.add(task)
                task.add_done_callback(agent_tasks.discard)

            elif data["type"] == "tool_result":
                # ③ 主循环还能收到 tool_result → resolve Future → 唤醒 Agent
                tool_result = ToolResultMessage(**data)
                bridge = session_manager.get_bridge(tool_result.thread_id)
                if bridge:
                    bridge.resolve_tool_result(tool_result)

    except WebSocketDisconnect:
        ...
```

**关键变化**：

```
修复后的时序：

主循环协程（专职接收）              后台 Agent 协程
────────────────────              ──────────────────
│                                  │
├─ receive_text() → user_input     │
├─ create_task(run_agent) ────────→│ 启动
├─ receive_text() → 挂起等消息      │ agent.ainvoke()
│   ...                            │   ├─ 调用工具
│   ...                            │   ├─ send_json(tool_call)
│   ...                            │   └─ await future → 挂起
│                                  │
├─ receive_text() → tool_result ◄──│ 前端返回结果
├─ resolve_tool_result(future) ───→│ future.set_result() → 唤醒！
├─ receive_text() → 挂起            │ Agent 继续执行
│   ...                            │ 发送 ai_message + end
│   ...                            │ Task 完成
│                                  │
```

**一句话总结**：主循环是"前台接待员"，只负责接收消息和分发；Agent 执行是"后台工作人员"，独立处理业务逻辑。通过 `asyncio.create_task()` 将两者分离，各司其职，不再相互阻塞。

### 11.6 ContextVar 跨 Context 错误详解

这是 `asyncio.create_task` 方案引入后暴露的**第二个并发陷阱**，涉及 Python ContextVar 的核心机制。

#### 11.6.1 Bug 现象

WebSocket 断开重连时，后台日志出现：

```
ERROR: Task exception was never retrieved
ValueError: <Token var=<ContextVar name='bridge_var' ...> was created in a different Context
```

完整日志时序：

```
1. Agent 开始执行 (thread_id=plugin_sceneAgent_4041271567)
2. 发送 tool_call: queryScenario
3. 收到 tool_result: success=False
4. WebSocket 断连 → bridge cleanup → 旧连接被替换 → 注册新连接
5. 💥 ValueError: Token was created in a different Context
```

#### 11.6.2 根因：`asyncio.create_task` 的 Context 复制机制

上一节（11.5）我们把 Agent 执行放进了 `asyncio.create_task(run_agent(...))`。但这引入了新问题 — **`asyncio.create_task` 会在创建时复制一份当前 Context**。

问题出在最初的代码写法：

```python
# ❌ 有 bug 的写法（ContextVar 跨 Context）
async def websocket_endpoint(websocket: WebSocket) -> None:
    ...
    async def run_agent(bridge, token, user_msg):
        """后台执行 agent"""
        try:
            agent = await _get_or_create_agent(...)
            result = await agent.ainvoke(...)       # 工具函数通过 bridge_var.get() 获取 bridge
            ...
        finally:
            bridge_var.reset(token)                 # ③ reset 在这里（子 Context）

    while True:
        raw = await websocket.receive_text()
        ...
        bridge = session_manager.register(thread_id, websocket)
        token = bridge_var.set(bridge)              # ① set 在这里（父 Context）
        task = asyncio.create_task(run_agent(bridge, token, user_msg))  # ② create_task 复制 Context
```

**发生了什么**：

```
父协程 (websocket_endpoint)             子协程 (run_agent, 经由 create_task)
─────────────────────────────          ─────────────────────────────────
                                       Context 是父 Context 的副本（独立对象）
bridge_var.set(bridge)                 但 token 属于父 Context！
  └─ 返回 token_A                      
     (token_A 绑定到父 Context)        
                                       try:
                                          ...
                                       finally:
                                          bridge_var.reset(token_A)
                                          └─ Python 检测: token_A 属于父 Context
                                             当前是子 Context → 不匹配
                                          💥 ValueError!
```

**核心矛盾**：`ContextVar.reset(token)` 要求 **token 必须在当前 Context 中创建**。但 `token` 在父协程由 `bridge_var.set()` 生成（绑定到父 Context），而在子协程（`asyncio.create_task` 创建，拥有独立 Context 副本）的 `finally` 中调用 `reset(token)`。

**这和 Java ThreadLocal 的关键区别**：

| | Java ThreadLocal | Python ContextVar |
|---|---|---|
| 子线程/Task 继承 | `InheritableThreadLocal` 复制值 | `create_task` 复制整个 Context |
| Token 归属 | 无此概念 | Token 绑定到**特定 Context 实例** |
| 跨线程 reset | 不适用 | ❌ 不允许（直接抛异常） |

#### 11.6.3 为什么"偶尔"才触发

从日志可以看到触发条件需要**同时满足**：

1. `run_agent` 还没执行完（Agent 正在处理 TLE 轨道数据，需要工具调用）
2. WebSocket 断连重连（前端刷新页面 / 网络波动）
3. 父协程的 `finally` 块执行 `task.cancel()` → 触发 `run_agent` 的 `CancelledError`
4. `run_agent` 的 `finally` 执行 `bridge_var.reset(token)` → 💥

如果 Agent 在断连前已经完成了（`run_agent` 正常退出，`finally` 执行完毕，Task 被 `done_callback` 从 `agent_tasks` 中移除），就不会触发。这就是为什么它看起来"偶发"。

#### 11.6.4 正确解法：set 和 reset 放在同一个 Context

**核心原则**：`bridge_var.set()` 和 `bridge_var.reset()` 必须在同一个协程中执行，不能被 `asyncio.create_task` 隔开。

```python
# ✅ 正确写法：set 和 reset 都在 run_agent 内部
async def websocket_endpoint(websocket: WebSocket) -> None:
    ...
    async def run_agent(bridge, user_msg):
        """后台执行 agent"""
        token = bridge_var.set(bridge)              # ① set 在这里（子 Context）
        try:
            agent = await _get_or_create_agent(...)
            result = await agent.ainvoke(...)
            ...
        finally:
            bridge_var.reset(token)                 # ② reset 在这里（同一个子 Context ✅）

    while True:
        raw = await websocket.receive_text()
        ...
        bridge = session_manager.register(thread_id, websocket)
        # 不再在父协程中 set，直接传 bridge 对象进去
        task = asyncio.create_task(run_agent(bridge, user_msg))
```

**变化**：

| | 修复前 | 修复后 |
|---|---|---|
| `bridge_var.set()` 位置 | 父协程（websocket_endpoint） | 子协程（run_agent 内部） |
| token 所属 Context | 父 Context | 子 Context |
| `bridge_var.reset()` 位置 | 子协程 finally | 子协程 finally |
| Context 匹配？ | ❌ 不匹配 → ValueError | ✅ 匹配 |

**一句话总结**：`ContextVar.set()` 和 `reset()` 永远放在同一个 `async def` 函数内，中间不要跨越 `asyncio.create_task` 边界。

---

## 12. CLI 工具：click

### 12.1 click 是什么

[click](https://click.palletsprojects.com/) 是 Python 的命令行工具库，Flask 作者开发，用装饰器定义命令。

### 12.2 本项目 CLI 结构

```python
import click

# 命令组（类似 git 的 "git xxx"）
@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """航天分析平台智能助手"""
    pass


# 子命令: space-aiagent run
@main.command()
@click.option("--host", default="0.0.0.0", help="服务器地址")
@click.option("--port", default=8028, help="服务器端口")
@click.option("--reload", is_flag=True, help="启用热重载")
def run(host: str, port: int, reload: bool) -> None:
    """启动 Web 服务器"""
    import uvicorn
    uvicorn.run("space_aiagent.main:app", host=host, port=port, reload=reload)


# 子命令组: space-aiagent skills ...
@main.group()
def skills() -> None:
    """Skill 管理命令"""
    pass


# 孙命令: space-aiagent skills list
@skills.command("list")
def skills_list() -> None:
    """列出所有已注册的 Skill"""
    from space_aiagent.skills import SkillRegistry
    registry = SkillRegistry()
    registry.discover()
    for summary in registry.get_summaries():
        click.echo(f"  {summary['name']}: {summary['description']}")


# 孙命令: space-aiagent skills show <name>
@skills.command("show")
@click.argument("name")
def skills_show(name: str) -> None:
    """查看指定 Skill 的详细信息"""
    # ... 实现
```

### 12.3 注册为可执行命令

```toml
# pyproject.toml
[project.scripts]
space-aiagent = "space_aiagent.cli:main"
```

安装后即可在终端直接使用：

```bash
space-aiagent --help
space-aiagent run --reload
space-aiagent skills list
space-aiagent skills show scene_management
```

---

## 13. 数据库：aiosqlite

### 13.1 aiosqlite 是什么

[aiosqlite](https://github.com/omnilib/aiosqlite) 是 SQLite 的**异步包装器**，让 SQLite 操作不阻塞事件循环。

> 为什么需要异步 SQLite？Python 的 `sqlite3` 是同步的，在高并发 WebSocket 应用中会阻塞事件循环，导致其他请求无法处理。

### 13.2 数据库管理类

```python
import aiosqlite
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class Database:
    def __init__(self, database_url: str) -> None:
        # 从 URL 解析文件路径
        # "sqlite+aiosqlite:///./data/space_aiagent.db" → "./data/space_aiagent.db"
        path = database_url.split("///")[-1]
        self.db_path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._checkpointer: AsyncSqliteSaver | None = None

    async def initialize(self) -> None:
        """初始化连接和表结构"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row          # 结果以 Row 对象返回
        await self._db.execute("PRAGMA journal_mode=WAL")    # 写前日志，提高并发
        await self._db.execute("PRAGMA foreign_keys=ON")     # 启用外键约束

    async def close(self) -> None:
        """关闭连接"""
        if self._checkpointer:
            self._checkpointer = None
        if self._db:
            await self._db.close()
            self._db = None

    async def get_checkpointer(self) -> AsyncSqliteSaver:
        """获取 LangGraph 的 checkpointer（用于 Agent 会话状态持久化）

        AsyncSqliteSaver 将 Agent 消息历史以 checkpoint 形式保存到 SQLite，
        支持跨轮次记忆和进程重启恢复。只需调用 setup() 即可自动建表。
        """
        if self._checkpointer is None:
            if self._db is None:
                await self.initialize()
            self._checkpointer = AsyncSqliteSaver(conn=self._db)
            await self._checkpointer.setup()         # 自动创建 checkpoint 表
        return self._checkpointer
```

### 13.3 单例访问

```python
_db: Database | None = None

async def get_db() -> Database:
    """懒加载数据库单例"""
    global _db
    if _db is None:
        import os
        db_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(db_dir, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_dir}/space_aiagent.db"
        _db = Database(db_url)
        await _db.initialize()
    return _db
```

**Java 类比**：

| Python | Java (Spring Boot) |
|--------|--------------------|
| `aiosqlite` | H2 数据库（嵌入式） |
| `aiosqlite.connect()` | `DataSource.getConnection()` |
| `await db.execute("SQL")` | `jdbcTemplate.update("SQL")` |
| PRAGMA | 数据库配置参数 |
| `AsyncSqliteSaver` | JPA Repository + 状态表 |

---

## 14. 代码质量：ruff + pre-commit

### 14.1 ruff — 一站式 Python 工具

[ruff](https://docs.astral.sh/ruff/) 是 Rust 编写的极速 Python linter + formatter，是大一统方案：

```
ruff ═══ 替代 flake8 + isort + pyupgrade + autoflake + ...
ruff format ═══ 替代 black
```

### 14.2 pyproject.toml 配置

```toml
[tool.ruff]
target-version = "py313"
line-length = 120                 # 每行最大 120 字符（默认 88）
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes（检查未使用的导入）
    "I",    # isort（自动排序导入）
    "N",    # pep8-naming（命名规范）
    "UP",   # pyupgrade（语法升级建议）
    "B",    # flake8-bugbear（常见 bug 检测）
    "SIM",  # flake8-simplify（简化建议）
    "TCH",  # flake8-type-checking（类型导入建议）
    "RUF",  # ruff 自有规则
]
ignore = ["E501", "RUF001", "RUF002", "RUF003"]   # 忽略特定规则

[tool.ruff.format]
quote-style = "double"           # 使用双引号
indent-style = "space"           # 空格缩进
```

### 14.3 使用命令

```bash
# 检查（不修改）
ruff check src/ tests/

# 格式化
ruff format src/ tests/

# 修复（自动修复可修复的问题）
ruff check --fix src/ tests/
```

### 14.4 pre-commit — Git hooks

[pre-commit](https://pre-commit.com/) 在每次 `git commit` 前自动运行检查：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff           # 自动检查
      - id: ruff-format    # 自动格式化
```

```bash
# 安装 hooks
pre-commit install

# 之后每次 git commit 会自动运行 ruff
```

---

## 15. 测试：pytest + pytest-asyncio

### 15.1 pytest 基础

[pytest](https://docs.pytest.org/) 是 Python 最流行的测试框架。

```python
# tests/test_config.py
from space_aiagent.infrastructure.config import get_settings


def test_get_settings_returns_singleton():
    """测试 get_settings 返回单例"""
    settings1 = get_settings()
    settings2 = get_settings()
    assert settings1 is settings2          # 同一个对象


def test_server_defaults():
    """测试服务器默认配置"""
    settings = get_settings()
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 8028


def test_resolve_env_vars():
    """测试环境变量解析"""
    from space_aiagent.infrastructure.config import _resolve_env_vars
    import os
    os.environ["TEST_VAR"] = "resolved"
    result = _resolve_env_vars({"key": "${TEST_VAR:default}"})
    assert result["key"] == "resolved"
```

### 15.2 pytest-asyncio — 异步测试

```python
# tests/test_database.py
import pytest
from space_aiagent.infrastructure.database import Database

@pytest.mark.asyncio
async def test_database_initialize(tmp_path):
    """测试数据库初始化"""
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")

    await db.initialize()
    assert db_path.exists()

    await db.close()
```

### 15.3 运行测试

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"       # 自动检测 async 测试函数，无需手动添加标记
pythonpath = ["src"]        # 让 pytest 能找到 src 下的包
```

```bash
# 运行所有测试
pytest

# 运行特定文件
pytest tests/test_config.py

# 带覆盖率
pytest --cov=src/space_aiagent --cov-report=html

# 详细输出
pytest -v
```

---

## 16. 项目结构最佳实践

### 16.1 Python 包管理：pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "space-aiagent"
version = "0.1.0"
requires-python = ">=3.13"

# 生产依赖
dependencies = [
    "fastapi>=0.136.3",
    "pydantic>=2.13.4",
    "deepagents>=0.6.8",
    # ...
]

# 开发依赖（可选组）
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "ruff>=0.8.0",
    "pre-commit>=4.0.0",
]

# CLI 入口
[project.scripts]
space-aiagent = "space_aiagent.cli:main"

# 包发现
[tool.setuptools.packages.find]
where = ["src"]

# 打包非 Python 文件
[tool.setuptools.package-data]
space_aiagent = ["prompts/*.md", "knowledge/*.md"]
```

### 16.2 `src` 布局

项目采用 `src/` 布局（"src layout"），好处是：
- **防止意外导入**：必须 `pip install -e .` 后才能导入
- **强制可安装性**：确保代码始终可通过 `import space_aiagent` 访问

```
project/
├── src/                          # 所有 Python 源码放这里
│   └── space_aiagent/            # 顶层包
│       ├── __init__.py
│       ├── main.py
│       ├── api/
│       ├── agents/
│       ├── skills/
│       └── ...
├── config/                       # 配置文件（非 Python）
├── tests/                        # 测试
├── readme/                       # 文档
├── pyproject.toml                # 项目配置
└── .env                          # 环境变量（不提交 Git）
```

### 16.3 导入规范

```python
# ✅ 绝对导入（项目内推荐）
from space_aiagent.models.enums import EntityType
from space_aiagent.infrastructure.config import get_settings

# ✅ 标准库
import json
import logging
from pathlib import Path

# ✅ 第三方库
from fastapi import APIRouter, WebSocket
from pydantic import BaseModel, Field

# ⚠️ 延迟导入（用于有循环导入风险或 CLI 场景）
def some_func():
    from space_aiagent.skills import SkillRegistry   # 在函数内导入
    ...
```

### 16.4 虚拟环境

```bash
# 创建
python3.13 -m venv .venv           # Java 的 target/ 类比

# 激活
source .venv/bin/activate          # macOS/Linux

# 安装（开发模式，代码修改立即生效）
pip install -e ".[dev]"            # -e = editable，类似 Java 的 exploded deploy

# 导出依赖（供 CI/CD 使用）
pip freeze > requirements.txt
```

---

## 附录：常用命令速查

```bash
# 虚拟环境
source .venv/bin/activate                   # 激活
deactivate                                  # 退出

# 包管理
pip install -e ".[dev]"                     # 安装项目 + 开发依赖
pip list                                    # 查看已安装的包

# 运行
python -m space_aiagent.main                # 启动服务
space-aiagent run --reload                  # CLI 启动

# 代码质量
ruff check src/ tests/                      # 检查
ruff format src/ tests/                     # 格式化

# 测试
pytest                                      # 运行全部
pytest -v                                   # 详细输出
pytest --cov=src/space_aiagent              # 带覆盖率

# Git hooks
pre-commit install                          # 安装 hooks
pre-commit run --all-files                  # 手动运行
```

---

## 17. AgentMiddleware 中间件深度讲解

### 17.1 什么是中间件

中间件（Middleware）是一种**洋葱模型（Onion Model）**的架构模式，在请求/响应的处理链路中插入可组合的处理层。每一层可以在调用前、调用后、甚至替代调用本身执行逻辑。

```
             ┌──────────────────────────┐
             │     Middleware A          │
             │  ┌────────────────────┐   │
             │  │   Middleware B      │   │
             │  │  ┌──────────────┐   │   │
             │  │  │  Core Logic   │   │   │
             │  │  │  (LLM/Tool)   │   │   │
             │  │  └──────────────┘   │   │
             │  └────────────────────┘   │
             └──────────────────────────┘
           请求 → A → B → Core → B → A → 响应
```

在 LangChain / deepagents 中，`AgentMiddleware` 是这个模式的实现 —— 它不处理 HTTP 请求，而是**拦截 Agent 的 LLM 调用和工具调用**。

**中间件的核心价值**：

| 特性 | 说明 |
|------|------|
| **关注点分离** | 日志、限流、认证等横切关注点从业务代码中剥离 |
| **可组合** | 多个中间件可以链式组合，顺序可控 |
| **可复用** | 同一个中间件可以用于不同的 Agent |
| **无侵入** | 不需要修改业务代码（工具函数、prompt） |

### 17.2 AgentMiddleware 架构

`AgentMiddleware` 提供了两类钩子：

**生命周期钩子** — 在 Agent 执行的特定阶段触发，成为 graph 中的独立节点：

```
START → before_agent → before_model → model → after_model → tools
                ↑                                    │
                └────────────────────────────────────┘
                                      ↓
                                 after_agent → END
```

| 钩子 | 触发时机 | 典型用途 |
|------|---------|---------|
| `before_agent` | Agent 开始前（一次） | 初始化状态、注入上下文 |
| `after_agent` | Agent 结束后（一次） | 校验结果、触发后续流程、循环回 model |
| `before_model` | 每次 LLM 调用前 | 修改消息、动态注入 prompt、检查 token 预算 |
| `after_model` | 每次 LLM 调用后 | 校验输出、提取信息 |

**包装器钩子** — 以函数组合方式包裹 LLM 和 Tool 调用，不产生 graph 节点：

```
wrap_model_call:  outer(inner(execute_model))
wrap_tool_call:   outer(inner(execute_tool))
```

| 钩子 | 签名 | 典型用途 |
|------|------|---------|
| `wrap_model_call` | `(request: ModelRequest, handler) -> ModelResponse` | 修改系统提示、重试、日志、限流 |
| `awrap_model_call` | 同上异步版本 | 同上（异步环境） |
| `wrap_tool_call` | `(request: ToolCallRequest, handler) -> ToolMessage` | 修改工具参数、重试、日志、参数校验 |
| `awrap_tool_call` | 同上异步版本 | 同上（异步环境） |

**包装器 vs 生命周期钩子的区别**：

| | 包装器 (`wrap_*`) | 生命周期 (`before_*/after_*`) |
|---|---|---|
| 实现方式 | 函数组合（洋葱） | graph 节点 |
| 能拦截 handler 吗 | 可以（多次调用 handler 做重试，或不调用做短路） | 不能直接拦截 |
| 性能开销 | 极小（无状态转换） | 每次触发一次节点转换 |
| 适合场景 | 日志、重试、参数修改 | 状态初始化、后置检查 |

**组合顺序**：中间件列表中的**第一个是最外层**，最后一个是最内层（最接近 LLM/Tool 调用）。

```python
middleware=[A(), B(), C()]
# 模型调用链：A.wrap_model_call → B.wrap_model_call → C.wrap_model_call → 实际 LLM
# 工具调用链：A.wrap_tool_call → B.wrap_tool_call → C.wrap_tool_call → 实际工具
```

### 17.3 本项目实战：LoggingMiddleware

本项目创建了 `LoggingMiddleware` 来解决 Agent 执行过程黑盒的问题。以下是完整实现和设计思路。

#### 17.3.1 为什么需要这个中间件

在 `astream_events` 中做日志有几个问题：
- 日志逻辑与 WebSocket 业务耦合在 `websocket.py` 中
- `astream_events` 只能看到顶层工具调用（`task`），看不到 LLM 决策和工具输入输出细节
- 无法在多个 Agent 实例间复用

下沉到中间件后：日志与 WebSocket 解耦、能拦截每次 LLM 调用和工具调用、未来换 Agent 类型也能复用。

#### 17.3.2 完整实现

```python
"""
文件: src/space_aiagent/middleware/logging.py
Agent 执行日志中间件 — 基于 AgentMiddleware 的 awrap_model_call + awrap_tool_call
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage

logger = logging.getLogger(__name__)


def _truncate(obj, max_len: int = 200) -> str:
    """截断过长字符串，用于日志输出"""
    s = str(obj)
    return s if len(s) <= max_len else s[:max_len] + f"...[截断, 总长{len(s)}]"


def _msg_preview(msg: BaseMessage, max_len: int = 120) -> dict:
    """提取消息预览信息"""
    content = str(getattr(msg, "content", ""))
    return {
        "type": getattr(msg, "type", "?"),
        "content": content[:max_len] + ("..." if len(content) > max_len else ""),
    }


class LoggingMiddleware(AgentMiddleware):
    """记录 Agent 执行过程的中间件"""

    state_schema = AgentState        # 声明此中间件使用的状态类型

    def __init__(self, thread_id: str = "") -> None:
        super().__init__()
        self.thread_id = thread_id
        self.step_count = 0          # LLM 调用次数
        self.tool_call_count = 0     # 工具调用次数

    # ── 拦截 LLM 调用 ──

    async def awrap_model_call(
        self,
        request: ModelRequest,        # 包含 messages, system_message, tools 等
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        self.step_count += 1
        messages = request.messages
        last_msgs = messages[-3:] if len(messages) > 3 else messages

        # 记录 LLM 输入上下文
        logger.debug(
            "[步骤 %d] LLM 调用, thread=%s, 上下文 %d 条消息",
            self.step_count, self.thread_id, len(messages),
        )
        logger.debug(
            "[步骤 %d] 最近消息: %s",
            self.step_count,
            [_msg_preview(m) for m in last_msgs],
        )

        response = await handler(request)    # 调用内层中间件或实际 LLM

        # 解析 LLM 输出的工具调用决策
        if isinstance(response, ModelResponse):
            result_messages = response.result
        elif isinstance(response, AIMessage):
            result_messages = [response]
        else:
            result_messages = []

        for msg in result_messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    logger.info(
                        "[步骤 %d] LLM 决定调用工具: %s(%s)",
                        self.step_count,
                        tc.get("name", "?"),
                        _truncate(tc.get("args", {}), 200),
                    )

        return response

    # ── 拦截工具调用 ──

    async def awrap_tool_call(
        self,
        request,                      # 包含 tool_call (name + args), tool, state, runtime
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        self.tool_call_count += 1
        tool_name = request.tool_call.get("name", "?")
        tool_args = request.tool_call.get("args", {})

        logger.info(
            "[工具 %d] 开始: %s, 参数: %s",
            self.tool_call_count, tool_name, _truncate(tool_args, 300),
        )

        result = await handler(request)     # 执行实际工具

        logger.info(
            "[工具 %d] 完成: %s, 结果: %s",
            self.tool_call_count, tool_name, _truncate(result, 200),
        )
        return result
```

#### 17.3.3 注入到 Agent

```python
# 文件: src/space_aiagent/agents/orchestrator.py
from space_aiagent.middleware import LoggingMiddleware

agent = create_deep_agent(
    model=model,
    system_prompt=system_prompt,
    subagents=subagents,
    middleware=[LoggingMiddleware()],    # ← 注入自定义中间件
    ...
)
```

**`create_deep_agent` 中的中间件顺序**：

```
1. TodoListMiddleware         ← deepagents 内置
2. SkillsMiddleware           ← deepagents 内置
3. FilesystemMiddleware       ← deepagents 内置
4. SubAgentMiddleware         ← deepagents 内置
5. SummarizationMiddleware    ← deepagents 内置
6. PatchToolCallsMiddleware   ← deepagents 内置
7. [用户自定义中间件]          ← LoggingMiddleware 在这里
8. MemoryMiddleware            ← deepagents 内置
9. HumanInTheLoopMiddleware    ← deepagents 内置
```

自定义中间件在 SubAgentMiddleware 之后、MemoryMiddleware 之前，意味着它能拦截 Orchestrator 的 LLM 调用和 `task` 工具调用，但不会拦截子 Agent 的内部调用（子 Agent 有自己独立的 graph）。

### 17.4 LangChain 内置中间件一览

LangChain 提供了一批开箱即用的中间件，覆盖常见需求：

| 中间件 | 文件 | 钩子 | 用途 |
|--------|------|------|------|
| `ToolRetryMiddleware` | `tool_retry.py` | `wrap_tool_call` | 工具调用失败时自动重试 |
| `ModelRetryMiddleware` | `model_retry.py` | `wrap_model_call` | LLM 调用失败时自动重试 |
| `ModelFallbackMiddleware` | `model_fallback.py` | `wrap_model_call` | LLM 主模型不可用时切换备用模型 |
| `ModelCallLimitMiddleware` | `model_call_limit.py` | `before_model` | 限制 LLM 调用次数，防止无限循环 |
| `ToolCallLimitMiddleware` | `tool_call_limit.py` | 状态检查 | 限制工具调用次数 |
| `SummarizationMiddleware` | `summarization.py` | `before_model` | 上下文过长时自动摘要压缩 |
| `HumanInTheLoopMiddleware` | `human_in_the_loop.py` | `before_model`/`after_model` | 在关键操作前暂停，等待人工确认 |
| `TodoListMiddleware` | `todo.py` | 工具注入 | 自动注入 `write_todos` / `read_todos` 工具 |
| `ContextEditingMiddleware` | `context_editing.py` | `before_model` | 动态编辑/裁剪上下文消息 |
| `PIIMiddleware` | `pii.py` | `before_model`/`wrap_model_call` | 检测和脱敏 PII 数据 |
| `LLMToolEmulator` | `tool_emulator.py` | `wrap_tool_call` | 用 LLM 模拟工具调用（测试用） |
| `LLMToolSelectorMiddleware` | `tool_selection.py` | `wrap_model_call` | 在 LLM 调用前过滤可用工具列表 |

### 17.5 如何编写自定义中间件

**方式一：类继承（推荐，功能最全）**

```python
from langchain.agents.middleware.types import AgentMiddleware, AgentState, ModelRequest, ModelResponse

class RateLimitMiddleware(AgentMiddleware):
    """LLM 调用限流中间件"""

    state_schema = AgentState

    def __init__(self, max_calls_per_minute: int = 10):
        super().__init__()
        self.max_calls = max_calls_per_minute
        self._call_times: list[float] = []

    async def awrap_model_call(self, request, handler):
        # 清理过期记录
        import time
        now = time.time()
        self._call_times = [t for t in self._call_times if now - t < 60]

        if len(self._call_times) >= self.max_calls:
            raise RuntimeError(f"LLM 调用频率超限: {self.max_calls}/分钟")

        self._call_times.append(now)
        return await handler(request)
```

**方式二：装饰器（快捷方式，适合简单逻辑）**

```python
from langchain.agents.middleware import wrap_tool_call

@wrap_tool_call(tools=[], name="ParameterValidator")
def validate_params(request, handler):
    """校验工具参数"""
    tool_name = request.tool_call["name"]
    args = request.tool_call["args"]

    if tool_name == "create_scenario" and not args.get("name"):
        # 返回错误而不调用实际工具（短路）
        from langchain_core.messages import ToolMessage
        return ToolMessage(
            content="场景名称不能为空",
            tool_call_id=request.tool_call["id"],
        )

    return handler(request)   # 正常调用
```

**方式三：使用 `@hook_config` 控制流程跳转**

```python
from langchain.agents.middleware import hook_config

class QualityGateMiddleware(AgentMiddleware):
    """质量门禁：LLM 输出不符合要求时自动重试"""

    @hook_config(can_jump_to=["model"])
    def after_agent(self, state, runtime):
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "content") and "需要更多信息" in str(last_msg.content):
            return {"jump_to": "model"}   # 回到 model 节点重新生成
        return None                        # 正常结束
```

### 17.6 企业级中间件应用场景

中间件是企业 Agent 架构中的核心扩展点。以下是典型应用场景和对应的基础框架代码。

#### 17.6.1 日志与可观测性

```python
class ObservabilityMiddleware(AgentMiddleware):
    """全链路可观测中间件：记录每次 LLM 和工具调用的耗时、token、状态"""

    def __init__(self, tracer):
        super().__init__()
        self.tracer = tracer   # OpenTelemetry / LangSmith / custom tracer

    async def awrap_model_call(self, request, handler):
        span = self.tracer.start_span("llm_call")
        span.set_attribute("message_count", len(request.messages))
        t0 = time.time()
        try:
            response = await handler(request)
            span.set_attribute("status", "success")
            span.set_attribute("tool_calls", len(response.tool_calls or []))
        except Exception as e:
            span.set_attribute("status", "error")
            span.set_attribute("error", str(e))
            raise
        finally:
            span.set_attribute("duration_ms", (time.time() - t0) * 1000)
            span.end()
        return response

    async def awrap_tool_call(self, request, handler):
        span = self.tracer.start_span("tool_call", tool=request.tool_call["name"])
        t0 = time.time()
        try:
            result = await handler(request)
            span.set_attribute("status", "success")
        except Exception as e:
            span.set_attribute("status", "error")
            raise
        finally:
            span.set_attribute("duration_ms", (time.time() - t0) * 1000)
            span.end()
        return result
```

#### 17.6.2 限流与断路器

```python
class CircuitBreakerMiddleware(AgentMiddleware):
    """断路器：连续失败 N 次后熔断，冷却期后尝试恢复"""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        super().__init__()
        self.threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self._failures = 0
        self._last_failure_time = 0.0

    async def awrap_model_call(self, request, handler):
        import time
        if self._failures >= self.threshold:
            if time.time() - self._last_failure_time < self.cooldown:
                raise RuntimeError("断路器已打开，拒绝 LLM 调用")
            self._failures = 0   # 冷却期满，尝试恢复

        try:
            result = await handler(request)
            self._failures = 0   # 成功后重置
            return result
        except Exception:
            self._failures += 1
            self._last_failure_time = time.time()
            raise
```

#### 17.6.3 认证与权限

```python
class AuthMiddleware(AgentMiddleware):
    """认证鉴权：校验用户权限，过滤不允许调用的工具"""

    def __init__(self, user_permissions: list[str]):
        super().__init__()
        self.permissions = set(user_permissions)

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool_call["name"]
        if tool_name not in self.permissions:
            from langchain_core.messages import ToolMessage
            return ToolMessage(
                content=f"权限不足：你无权调用 {tool_name}",
                tool_call_id=request.tool_call["id"],
            )
        return await handler(request)
```

#### 17.6.4 请求/响应转换

```python
class DataMaskingMiddleware(AgentMiddleware):
    """数据脱敏：工具返回结果中自动遮盖敏感字段（手机号、身份证等）"""

    PATTERNS = [
        (r"\b1[3-9]\d{9}\b", "***PHONE***"),        # 手机号
        (r"\b\d{17}[\dXx]\b", "***ID_CARD***"),       # 身份证
    ]

    async def awrap_tool_call(self, request, handler):
        import re
        result = await handler(request)
        if hasattr(result, "content"):
            content = result.content
            for pattern, replacement in self.PATTERNS:
                content = re.sub(pattern, replacement, content)
            result.content = content
        return result
```

#### 17.6.5 缓存与去重

```python
class CacheMiddleware(AgentMiddleware):
    """工具调用结果缓存：相同参数的工具调用直接返回缓存结果"""

    def __init__(self, ttl_seconds: float = 300):
        super().__init__()
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    async def awrap_tool_call(self, request, handler):
        import time, json
        key = json.dumps(request.tool_call, sort_keys=True)
        now = time.time()

        if key in self._cache:
            cached_time, cached_result = self._cache[key]
            if now - cached_time < self.ttl:
                return cached_result   # 命中缓存

        result = await handler(request)
        self._cache[key] = (now, result)
        return result
```

#### 17.6.6 审计与合规

```python
class AuditMiddleware(AgentMiddleware):
    """审计日志：记录所有敏感操作的完整调用链，满足合规要求"""

    def __init__(self, audit_log: Any):
        super().__init__()
        self.audit = audit_log

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool_call["name"]
        args = request.tool_call.get("args", {})

        # 只记录写操作（创建、修改、删除）
        if any(verb in tool_name for verb in ("create", "update", "delete", "clear")):
            self.audit.record({
                "action": tool_name,
                "params": args,
                "timestamp": datetime.now().isoformat(),
                "user": request.runtime.context.get("user_id"),
            })

        return await handler(request)
```

### 17.7 中间件在企业架构中的位置

在一个完整的 AI Agent 系统中，中间件横跨多个层次：

```
┌─────────────────────────────────────────────────────┐
│                    接入层 (Gateway)                   │
│  API Gateway Middleware: 认证、限流、路由              │
├─────────────────────────────────────────────────────┤
│                    应用层 (Application)               │
│  FastAPI Middleware: CORS、请求日志、异常处理          │
├─────────────────────────────────────────────────────┤
│                    Agent 层 (Agent)                   │
│  AgentMiddleware: 日志、断路器、审计、脱敏、缓存        │
│  ┌─────────────────────────────────────────────┐    │
│  │ Orchestrator Agent                          │    │
│  │  ├─ LoggingMiddleware                       │    │
│  │  ├─ CircuitBreakerMiddleware                │    │
│  │  └─ AuditMiddleware                          │    │
│  │       ↓ task tool                           │    │
│  │  Sub-Agent (scene-agent)                    │    │
│  │  ├─ LoggingMiddleware                       │    │
│  │  └─ DataMaskingMiddleware                   │    │
│  └─────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────┤
│                    LLM 层 (Model)                    │
│  ModelRetryMiddleware、ModelFallbackMiddleware        │
├─────────────────────────────────────────────────────┤
│                    工具层 (Tool)                     │
│  ToolRetryMiddleware、CacheMiddleware                 │
└─────────────────────────────────────────────────────┘
```

**中间件组合最佳实践**：

```python
# 典型的生产环境 Agent 中间件栈
agent = create_deep_agent(
    model=model,
    middleware=[
        ObservabilityMiddleware(tracer),       # 最外层：全链路追踪
        AuthMiddleware(permissions),           # 认证鉴权
        CircuitBreakerMiddleware(threshold=5), # 断路器
        LoggingMiddleware(thread_id=tid),      # 业务日志
        CacheMiddleware(ttl=300),             # 最内层：缓存
    ],
)
# 请求流：Observe → Auth → Circuit → Log → Cache → LLM/Tool
# 响应流：LLM/Tool → Cache → Log → Circuit → Auth → Observe
```

**关键原则**：

| 原则 | 说明 |
|------|------|
| **越通用的放外层** | 追踪、认证等横切关注点放在最外层 |
| **越接近业务的放内层** | 缓存、参数校验等工具相关逻辑放在内层 |
| **中间件应无状态** | 中间件实例可跨请求复用，避免存储请求级状态 |
| **失败快速** | 中间件检测到异常应快速抛出，不要在链路中积累延迟 |
| **可测试** | 每个中间件应能独立单元测试，不依赖完整 Agent |

---

## 18. Agent 结构化输出：response_format 详解

### 18.1 问题背景：LLM 响应一致性的挑战

在本项目的开发过程中，我们遇到了一个典型的企业级 AI Agent 问题：**同一个用户请求，多次调用 LLM 得到风格迥异的回复**。

例如用户两次输入"查看一下实体"：

- 第一次回复：详细列出了实体数量、名称、类型，并给出了后续操作建议
- 第二次回复：简单的"当前场景中有 3 个实体"

这种不一致在 **企业实践中是不可接受的**，原因如下：

| 风险 | 说明 |
|------|------|
| **用户体验差** | 用户无法预期系统行为，同一操作得到不同反馈 |
| **前端渲染困难** | 非结构化文本难以解析，前端无法做定制化 UI 展示 |
| **测试困难** | 无法编写可靠的自动化测试验证 Agent 行为 |
| **合规风险** | 金融、医疗等领域要求输出格式严格一致 |
| **下游集成脆弱** | 如果下游系统解析 Agent 输出做二次处理，不一致的格式会导致解析失败 |

### 18.2 方案演进：从 Prompt 工程到 API 级约束

我们经历了三个阶段，每一步都解决了前一方案的局限性：

```
阶段 1: Prompt 工程（纯文本指令）
  "请以 JSON 格式返回，不要附加任何文字"
  → 问题：LLM 不总是遵守，可能输出 "好的，以下是 JSON：{...}" 或混入自然语言

阶段 2: Regex 提取 JSON（后处理兜底）
  在 Agent 输出中正则搜索 JSON block 并解析
  → 问题：不可靠（格式错误直接报错）、无法保证 JSON schema、维护成本高

阶段 3: ToolStrategy（API 级约束）✅ 最终方案
  利用模型 tool calling API 强制输出结构化数据
  → 优势：API 级保证、Pydantic 校验、零额外 LLM 调用
```

#### 阶段 1 的典型问题

```python
# Prompt 中写：
"你必须只输出 JSON 格式，不要附加任何文字。格式：{\"status\": \"success\", ...}"

# LLM 实际可能输出：
"好的，我已经查询了场景中的实体，以下是结果：
{
  \"status\": \"success\",
  \"code\": \"ENTITIES_LIST\",
  \"summary\": \"当前场景有 3 个实体\"
}"
# ↑ 混入了自然语言，JSON 解析失败
```

#### 阶段 2 的典型问题

```python
import re
import json

def extract_json(text: str) -> dict | None:
    """正则提取 JSON block —— 不可靠的方案"""
    # 匹配 ```json ... ``` 或裸 {...}
    patterns = [r'```json\s*([\s\S]*?)```', r'(\{[\s\S]*\})']
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None  # 兜底失败，用户看到错误
```

这种方式存在根本性问题：
- **假阴性**：JSON 格式稍有偏差即解析失败（如 trailing comma、单引号）
- **假阳性**：匹配到非 Agent 响应的 JSON（如 LLM 讨论中的代码示例）
- **无 schema 校验**：即使解析成功，字段类型可能不正确
- **维护成本高**：每次修改 schema 都要同步更新 regex 逻辑

### 18.3 response_format 的三种方式

LangChain 提供了三种结构化输出策略，它们都通过 `create_deep_agent(response_format=...)` 参数使用：

```python
from langchain.agents.structured_output import (
    ToolStrategy,      # 方法 1
    ProviderStrategy,  # 方法 2
    AutoStrategy,      # 方法 3
)
```

#### 方法 1：ToolStrategy（推荐，最通用）

**原理**：将 Pydantic 模型转换为一个"合成工具"，强制模型在回合结束时"调用"该工具。工具参数即为结构化输出的字段。因为模型必须遵循 tool calling 的函数签名约束，天然具备 schema 校验。

```python
from pydantic import BaseModel, Field
from typing import Literal
from langchain.agents.structured_output import ToolStrategy

class AgentResponse(BaseModel):
    """Agent 结构化输出模型"""
    status: Literal["success", "error", "info", "confirm"] = Field(
        description="响应状态"
    )
    code: str = Field(description="响应码，如 NO_SCENE、ENTITIES_LIST")
    summary: str = Field(description="人类可读的摘要")
    details: dict | None = Field(default=None, description="详细数据")
    suggestions: list[str] = Field(default_factory=list, description="后续建议")

# 使用
agent = create_deep_agent(
    model=model,
    response_format=ToolStrategy(AgentResponse),
    # ...其他参数
)
```

**执行机制**：

```
用户消息 → Agent 执行工具调用 → 回合结束前
  → ToolStrategy 注入 "AgentResponse" 合成工具
  → 模型被迫 "调用" 这个工具，参数 = 结构化输出
  → ToolStrategy 拦截该调用，提取为 AgentResponse 实例
  → 返回: {"messages": [...], "structured_response": AgentResponse(...)}
```

**提取结构化响应**（使用 astream_events）：

```python
async for event in agent.astream_events(
    {"messages": [HumanMessage(content=user_input)]},
    config={"configurable": {"thread_id": thread_id}, "recursion_limit": 100},
    version="v2",
):
    kind = event["event"]
    data = event["data"]

    if kind == "on_chat_model_end":
        output = data.get("output")
        if hasattr(output, "tool_calls") and output.tool_calls:
            for tc in output.tool_calls:
                if tc.get("name") == "AgentResponse":
                    response = AgentResponse(**tc["args"])
                    # response 是已校验的 Pydantic 实例
                    print(response.status)   # "success"
                    print(response.code)     # "ENTITIES_LIST"
                    print(response.summary)  # "当前场景有 3 个实体"

    elif kind == "on_tool_end":
        # 备选提取点：某些 LLM 提供商的 tool_call 在此触发
        if event["name"] == "AgentResponse":
            response = AgentResponse(**data["output"])
```

**优点**：
- 不依赖 LLM 提供商的原生 structured output 支持，任何支持 function calling 的模型都能用
- Pydantic 自动校验字段类型
- 与 deepagents 深度集成，`structured_response` 自动附加在结果中
- 兼容 DeepSeek、Qwen、OpenAI、Anthropic 等主流模型

**缺点**：
- 多一次"工具调用"（合成工具），消耗少量 token
- 某些模型的 tool_choice 支持有限（如不支持 `tool_choice="required"`）

#### 方法 2：ProviderStrategy（依赖 LLM 原生能力）

**原理**：使用 LLM 提供商的原生 JSON mode 或 structured output API（如 OpenAI 的 `response_format: {"type": "json_schema", "schema": {...}}`）。

```python
from langchain.agents.structured_output import ProviderStrategy

agent = create_deep_agent(
    model=model,
    response_format=ProviderStrategy(AgentResponse),
    # ...
)
```

**执行机制**：

```
用户消息 → Agent 执行工具调用
  → 最终回复时，ProviderStrategy 将 AgentResponse schema
    注入 LLM 调用的 response_format 参数
  → LLM 提供商保证输出符合 schema（API 层面）
  → 解析为 AgentResponse 实例
```

**优点**：
- 不消耗额外的 tool calling token
- 比 tool calling 更直接

**缺点**：
- **依赖 LLM 提供商支持**。DeepSeek 的 JSON mode 支持有限，Qwen DashScope 的支持程度因模型而异
- **兼容性差**。使用 OpenAI 的 `json_schema` 格式，切换到 DeepSeek 时可能不可用
- **无法中途切换**。一旦选择 ProviderStrategy，切换 LLM 提供商时需要重新验证

**适用场景**：
- 使用 OpenAI GPT-4o/GPT-4.1 等明确支持 structured output 的模型
- 对 token 消耗极度敏感的场景

#### 方法 3：AutoStrategy（自动选择）

**原理**：自动检测 LLM 提供商的能力，优先使用 `ProviderStrategy`，不支持时降级为 `ToolStrategy`。

```python
from langchain.agents.structured_output import AutoStrategy

agent = create_deep_agent(
    model=model,
    response_format=AutoStrategy(AgentResponse),
    # ...
)
```

**适用场景**：
- 需要兼容多个 LLM 提供商的平台
- 不确定目标模型是否支持原生 structured output

**注意**：自动检测机制依赖 LangChain 内部的模型能力注册表，部分国产模型可能未被正确识别，导致行为不符合预期。

### 18.4 企业开发中的选择指南

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| **通用企业应用**（本项目） | `ToolStrategy` | 兼容 DeepSeek/Qwen/OpenAI，不绑定特定提供商 |
| **OpenAI 专属应用** | `ProviderStrategy` | 利用原生支持，零额外开销 |
| **多模型平台/SaaS** | `AutoStrategy` | 自动适配不同模型能力 |
| **原型验证 / PoC** | `ToolStrategy` | 最保险，切换模型无顾虑 |
| **高并发低延迟** | `ProviderStrategy` | 减少 token 消耗和延迟 |

**本项目选择 ToolStrategy 的原因**：

1. 需要同时支持 DeepSeek（主力模型）和 Qwen（备用模型）
2. DeepSeek 的 structured output 能力不确定，ToolStrategy 只依赖 function calling（DeepSeek 支持良好）
3. 省去 ProviderStrategy 不可用时的回退逻辑
4. 额外 token 消耗（合成工具）在实际业务中可忽略

### 18.5 实战：模板化渲染

有了 `ToolStrategy` 保证的结构化输出后，下一步是**模板化渲染**——将 `AgentResponse` 转为一致的自然语言：

```python
# response_renderer.py
from space_aiagent.models.response_schema import AgentResponse

class _SafeDict(dict):
    """安全的格式化字典：缺失键返回占位符而非抛出 KeyError"""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"

DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    ("error", "NO_SCENE"): (
        "当前**尚未创建任何场景**，因此场景中没有任何实体。\n\n"
        "场景是所有航天任务实体的载体，需要先创建场景才能添加和管理实体。\n\n"
        "**接下来您可以：**\n"
        '- **创建场景** — 告诉我「创建场景」或「新建一个场景」\n'
        "- 场景创建后，即可添加卫星（基于 TLE）、地面站、传感器等实体\n\n"
        "请问需要先创建一个场景吗？"
    ),
    ("success", "ENTITIES_LIST"): (
        "当前场景 **{scene_name}** 共有 **{count}** 个实体：\n\n"
        "{entity_list}"
    ),
    ("success", "SCENE_CREATED"): (
        "场景 **「{scene_name}」** 已创建成功！\n\n"
        "现在可以在此场景中添加实体了。\n\n"
        "**接下来您可以：**\n"
        "- 添加卫星 — 提供 TLE 两行根数\n"
        "- 添加地面站、传感器等实体"
    ),
    ("success", "ENTITY_ADDED"): (
        "实体 **「{name}」**（类型: {entity_type}）已成功添加到场景 **{scene_name}** 中。"
    ),
}

class ResponseRenderer:
    """Agent 响应渲染器：模板命中 → 一致输出"""

    def render(self, response: AgentResponse) -> str:
        key = (response.status, response.code)
        template = self._templates.get(key)

        if template is not None:
            try:
                result = template.format_map(_SafeDict(response.details or {}))
                # 如果有未填充的占位符，降级处理
                if "{" not in result:
                    return result
            except Exception:
                pass  # 降级到通用回复

        # 降级：用 summary + suggestions 组装
        parts = [response.summary]
        if response.suggestions:
            parts.append("\n\n**接下来您可以：**")
            for s in response.suggestions:
                parts.append(f"- {s}")
        return "\n".join(parts)
```

**效果对比**：

```
# 使用前（阶段 1/2）：同一请求 3 次，3 种不同回复
- "当前场景中有 3 个实体"
- "场景 test 共包含 3 个实体：卫星1、卫星2、地面站1。您可以进行以下操作..."
- "查询结果：共 3 个实体 ✓"

# 使用后（阶段 3）：同一请求 3 次，回复完全一致
- "当前场景 **测试场景** 共有 **3** 个实体：

1. 卫星A（类型: satellite）
2. 卫星B（类型: satellite）
3. 地面站1（类型: groundStation）"
```

### 18.6 完整流程：ToolStrategy + ResponseRenderer

本项目的完整结构化输出流程：

```
用户输入 "查看实体"
       │
       ▼
┌──────────────────────────────────────────┐
│  Orchestrator Agent                      │
│  response_format=ToolStrategy(AgentResponse) │
│  ↓                                       │
│  1. 意图识别 → 委派 Entity Agent 查询实体   │
│  2. 收到查询结果                           │
│  3. ToolStrategy 强制 LLM 输出 AgentResponse│
│     {                                     │
│       "status": "success",                │
│       "code": "ENTITIES_LIST",             │
│       "summary": "当前场景有 3 个实体",     │
│       "details": {                        │
│         "scene_name": "测试场景",          │
│         "count": 3,                       │
│         "entity_list": "1. 卫星A\n2. ..." │
│       }                                   │
│     }                                     │
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│  ResponseRenderer.render()               │
│  key = ("success", "ENTITIES_LIST")      │
│  → 命中模板，填充 details 字段             │
│  → 输出一致的自然语言文本                   │
└──────────────────┬───────────────────────┘
                   ▼
          "当前场景 **测试场景** 共有 **3** 个实体：
           
           1. 卫星A（类型: satellite）
           2. 卫星B（类型: satellite）
           3. 地面站1（类型: groundStation）"
```

### 18.7 实践中踩过的坑

#### 坑 1：Agent 不调用合成工具

**现象**：使用 `ToolStrategy` 后，`structured_response` 始终为 `None`。

**原因**：某些模型（尤其是小模型）对 tool calling 的支持不完整，不总是调用 AgentResponse 合成工具。

**解决**：从 `on_chat_model_end` 事件中提取 tool_calls（ToolStrategy 在模型节点拦截，不走 ToolNode）：

```python
# ❌ 错误：只在 on_tool_end 中提取
if kind == "on_tool_end" and event["name"] == "AgentResponse":
    response = AgentResponse(**data["output"])

# ✅ 正确：同时在 on_chat_model_end 中提取
if kind == "on_chat_model_end":
    output = data.get("output")
    if hasattr(output, "tool_calls") and output.tool_calls:
        for tc in output.tool_calls:
            if tc.get("name") == "AgentResponse":
                response = AgentResponse(**tc["args"])
```

#### 坑 2：recursion_limit 不匹配

**现象**：`ainvoke` 正常但 `astream_events(v2)` 报 `GraphRecursionError`。

**原因**：
- `ainvoke` 使用 LangGraph 的 `ensure_config`（默认 `recursion_limit=10007`）
- `astream_events(v2)` 使用 `langchain_core` 的 `ensure_config`（默认 `recursion_limit=25`）

**解决**：在 config 中显式设置 `recursion_limit`：

```python
config = {
    "configurable": {"thread_id": thread_id},
    "recursion_limit": 100,  # 显式设置，覆盖默认的 25
}
```

#### 坑 3：details 字段 LLM 输出 JSON 字符串

**现象**：`AgentResponse.details` 应为 `dict`，但 LLM 输出 `"{\"scene_name\": \"test\"}"`（JSON 字符串）。

**原因**：LLM 在处理嵌套对象时，有时将其序列化为 JSON 字符串而非直接输出对象。

**解决**：在 Pydantic 模型中使用 `field_validator` 容错处理：

```python
class AgentResponse(BaseModel):
    details: dict | None = None

    @field_validator("details", mode="before")
    @classmethod
    def _parse_details(cls, v: dict | str | None) -> dict | None:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return None
```

#### 坑 4：Python 字符串中的中文引号

**现象**：`"创建场景"` 中的中文书名号 `"` `"` 被 Python 解释为字符串边界，导致语法错误。

**原因**：中文全角引号 `"`（U+201C）和 `"`（U+201D）在某些编辑器中与 ASCII 引号难以区分。

**解决**：使用全角书名号 `「」` 或单引号替代：

```python
# ❌ 可能出错
"场景 **"创建场景"** 已成功"

# ✅ 安全
"场景 **「创建场景」** 已成功"
```

### 18.8 小结

| 维度 | Prompt 工程 | Regex 后处理 | ToolStrategy |
|------|-----------|-------------|-------------|
| **可靠性** | 低（LLM 不总是遵守） | 中（格式错误则失败） | 高（API 级保证） |
| **Schema 校验** | 无 | 手动 | 自动（Pydantic） |
| **维护成本** | 低（但不可靠） | 高（同步 schema + regex） | 低（改 Pydantic 即可） |
| **额外 LLM 调用** | 0 | 0 | 0（合成工具在回合内） |
| **兼容性** | 所有模型 | 所有模型 | 支持 function calling 的模型 |
| **一致性** | 差 | 中（模板化后好） | 极好（模板化后 100% 一致） |
| **适用场景** | 原型验证 | 过渡方案 | **企业生产** |

**核心经验**：在企业级 AI Agent 开发中，**不要依赖 LLM 的文本格式遵守能力**。结构化输出应该通过 API 级机制（ToolStrategy / ProviderStrategy）强制保证，而非通过 prompt 指令"请求"模型配合。这是区分原型和生产级系统的关键分界线之一。

---

## 19. 标准库：os 模块常用方法

### 19.1 os 模块是什么

`os` 是 Python 标准库中与操作系统交互的模块，提供文件/目录操作、环境变量、进程管理等功能。本项目中 `database.py` 就用到了 `os.path.join()` 和 `os.getcwd()`。

### 19.2 核心陷阱：`os.getcwd()` 的路径不确定问题

`os.getcwd()` 返回的是**程序启动时的工作目录**，而不是代码文件所在的目录。这意味着同一个程序从不同目录启动，`getcwd()` 返回不同的值：

```python
import os

# 假设项目路径: /Users/dev/space-aiagent-v1/

# 情况 1: 从项目根目录启动
# $ cd /Users/dev/space-aiagent-v1 && python -m space_aiagent.main
os.getcwd()  # → '/Users/dev/space-aiagent-v1'
os.path.join(os.getcwd(), "data")  # → '/Users/dev/space-aiagent-v1/data'  ✅ 正确

# 情况 2: 从 src/space_aiagent/ 目录启动
# $ cd /Users/dev/space-aiagent-v1/src/space_aiagent && python -m space_aiagent.main
os.getcwd()  # → '/Users/dev/space-aiagent-v1/src/space_aiagent'
os.path.join(os.getcwd(), "data")  # → '/Users/dev/space-aiagent-v1/src/space_aiagent/data'  ❌ 错误路径
```

**本项目踩过的坑**：`database.py` 的 `get_db()` 用了 `os.path.join(os.getcwd(), "data")` 来定位数据库目录。某次从 `src/space_aiagent/` 目录下启动程序，导致数据库文件创建在了错误位置，出现了两份 `space_aiagent.db`（一份在 `data/`，一份在 `src/space_aiagent/data/`）。

**正确做法**：用 `pathlib.Path(__file__)` 基于代码文件位置计算路径，不依赖启动目录：

```python
from pathlib import Path

# __file__ 是当前文件的绝对路径，不受启动目录影响
# database.py 位于 src/space_aiagent/infrastructure/database.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# → /Users/dev/space-aiagent-v1/（永远正确）

db_dir = _PROJECT_ROOT / "data"  # 始终是 项目根目录/data
```

### 19.3 路径操作

```python
import os

# ── 拼接路径（跨平台，自动处理 / 和 \） ──
os.path.join("/home/user", "data", "db.sqlite")
# → '/home/user/data/db.sqlite'

# ── 获取绝对路径 ──
os.path.abspath("./data/db.sqlite")
# → '/home/user/project/data/db.sqlite'

# ── 拆分路径 ──
os.path.dirname("/home/user/data/db.sqlite")   # → '/home/user/data'
os.path.basename("/home/user/data/db.sqlite")   # → 'db.sqlite'
os.path.split("/home/user/data/db.sqlite")      # → ('/home/user/data', 'db.sqlite')

# ── 分离扩展名 ──
os.path.splitext("config.yaml")                  # → ('config', '.yaml')

# ── 判断路径类型 ──
os.path.exists("/home/user/data")     # 路径是否存在 → True / False
os.path.isfile("/home/user/data/db")  # 是否文件 → True / False
os.path.isdir("/home/user/data")      # 是否目录 → True / False

# ── 获取文件信息 ──
os.path.getsize("/home/user/data/db.sqlite")  # 文件大小（字节）
os.path.getmtime("/home/user/data/db.sqlite") # 最后修改时间（时间戳）
```

**推荐**：新代码优先用 `pathlib.Path`，比 `os.path` 更直观：

```python
from pathlib import Path

p = Path("/home/user/data/db.sqlite")

p.parent          # → Path('/home/user/data')    等价于 os.path.dirname
p.name            # → 'db.sqlite'               等价于 os.path.basename
p.suffix          # → '.sqlite'                 等价于 os.path.splitext[1]
p.stem            # → 'db'                      文件名（不含扩展名）
p.exists()        # → True / False              等价于 os.path.exists
p.is_file()       # → True / False              等价于 os.path.isfile
p.is_dir()        # → True / False              等价于 os.path.isdir
p.stat().st_size  # → 文件大小                   等价于 os.path.getsize

# / 运算符拼接路径（比 os.path.join 更直观）
data_dir = Path(__file__).parent / "data"
```

### 19.4 目录操作

```python
import os

# ── 创建目录 ──
os.makedirs("/home/user/data/logs", exist_ok=True)
# 递归创建，已存在不报错（类似 mkdir -p）

# ── 列出目录内容 ──
os.listdir("/home/user/data")
# → ['db.sqlite', 'logs', 'config.yaml']    只返回文件名

# ── 遍历目录树 ──
for root, dirs, files in os.walk("/home/user/project"):
    for f in files:
        print(os.path.join(root, f))
# 递归遍历所有子目录和文件

# ── 重命名 / 移动 ──
os.rename("old_name.txt", "new_name.txt")

# ── 删除 ──
os.remove("temp.txt")          # 删除文件
os.rmdir("empty_dir")          # 删除空目录
# 删除非空目录用 shutil.rmtree("dir_path")
```

**pathlib 对应写法**：

```python
from pathlib import Path

Path("data/logs").mkdir(parents=True, exist_ok=True)  # 等价于 os.makedirs
[p.name for p in Path("data").iterdir()]               # 等价于 os.listdir
Path("old.txt").rename("new.txt")                      # 等价于 os.rename
Path("temp.txt").unlink()                               # 等价于 os.remove
```

### 19.5 环境变量

```python
import os

# ── 读取环境变量 ──
os.environ.get("HOME")                        # → '/Users/dev'
os.environ.get("DATABASE_URL")                # → None（不存在时）
os.environ.get("PORT", "8080")                # → '8080'（带默认值）
os.getenv("PORT", "8080")                     # 同上，更简洁

# ── 设置环境变量（仅当前进程有效，不修改系统环境） ──
os.environ["MY_VAR"] = "hello"
os.environ["PORT"] = "9090"

# ── 判断环境变量是否存在 ──
if "LLM_API_KEY" in os.environ:
    api_key = os.environ["LLM_API_KEY"]

# ── 删除环境变量 ──
del os.environ["MY_VAR"]
```

**本项目中的用法**：

```python
# infrastructure/config.py — 读取 APP_ENV 环境变量决定加载哪套配置
env = os.getenv("APP_ENV", "dev")    # 默认 dev 环境

# .env 文件通过 python-dotenv 加载后，os.environ 中就有对应值
# load_dotenv() → 将 .env 文件中的键值对写入 os.environ
```

### 19.6 进程与系统信息

```python
import os

# ── 进程信息 ──
os.getpid()        # 当前进程 ID → 12345
os.getppid()       # 父进程 ID → 12340
os.getcwd()        # 当前工作目录 → '/home/user/project'

# ── 执行 Shell 命令（简单场景） ──
exit_code = os.system("ls -la")          # 返回退出码，无法获取输出
# 复杂场景推荐用 subprocess 模块

# ── 环境信息 ──
os.name             # → 'posix' (Linux/Mac) 或 'nt' (Windows)
os.sep              # → '/' (Linux/Mac) 或 '\' (Windows)
os.linesep          # → '\n' (Linux/Mac) 或 '\r\n' (Windows)
os.cpu_count()      # → CPU 核心数
```

### 19.7 常用方法速查表

| 分类 | 方法 | 作用 | 推荐替代 |
|------|------|------|---------|
| **路径** | `os.getcwd()` | 获取当前工作目录 | `Path.cwd()` |
| | `os.path.join(a, b)` | 拼接路径 | `Path(a) / b` |
| | `os.path.exists(p)` | 路径是否存在 | `Path(p).exists()` |
| | `os.path.isfile(p)` | 是否文件 | `Path(p).is_file()` |
| | `os.path.isdir(p)` | 是否目录 | `Path(p).is_dir()` |
| | `os.path.abspath(p)` | 绝对路径 | `Path(p).resolve()` |
| | `os.path.dirname(p)` | 目录部分 | `Path(p).parent` |
| | `os.path.basename(p)` | 文件名部分 | `Path(p).name` |
| **目录** | `os.makedirs(p)` | 递归创建目录 | `Path(p).mkdir(parents=True)` |
| | `os.listdir(p)` | 列出目录内容 | `Path(p).iterdir()` |
| | `os.remove(p)` | 删除文件 | `Path(p).unlink()` |
| **环境** | `os.getenv(k, d)` | 读取环境变量 | — |
| | `os.environ[k] = v` | 设置环境变量 | — |
| **进程** | `os.getpid()` | 当前进程 ID | — |
| | `os.system(cmd)` | 执行 Shell 命令 | `subprocess.run()` |

### 19.8 本项目中的实际用法

```python
# 1. database.py — 读取环境变量获取数据库路径
import os

async def get_db() -> Database:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # 默认路径：用 pathlib 基于代码位置计算，不依赖 os.getcwd()
        db_dir = _PROJECT_ROOT / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_dir}/space_aiagent.db"
    ...


# 2. config.py — 读取 APP_ENV 环境变量
import os

env = os.getenv("APP_ENV", "dev")    # 环境名：dev / staging / prod
api_key = os.getenv("LLM_API_KEY")   # LLM API 密钥（从 .env 加载）
base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")


# 3. config.py — YAML 中的 ${VAR:default} 解析
import os

def _resolve_env_vars(value):
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) or ""),
            value
        )
    ...
```

### 19.9 os vs pathlib 选择指南

```python
# ── 新项目推荐 pathlib ──
from pathlib import Path

# 链式调用，代码更清晰
config_path = Path(__file__).parent.parent / "config" / "application.yaml"
if config_path.exists():
    content = config_path.read_text(encoding="utf-8")

# ── 以下场景仍需用 os ──
# 1. 环境变量：os.getenv / os.environ（pathlib 不提供此功能）
# 2. 进程信息：os.getpid / os.system（pathlib 不涉及）
# 3. 递归遍历：os.walk()（pathlib 的 rglob("*") 可以替代但灵活性略低）
# 4. 兼容旧代码：已有的 os.path 代码不需要重写
```

**Java 对比**：

| Python `os` | Java 对应 |
|------------|----------|
| `os.getenv("KEY")` | `System.getenv("KEY")` |
| `os.path.exists(path)` | `Files.exists(Path.of(path))` |
| `os.makedirs(dir)` | `Files.createDirectories(Path.of(dir))` |
| `os.listdir(dir)` | `Files.list(Path.of(dir))` |
| `os.remove(file)` | `Files.delete(Path.of(file))` |
| `os.getcwd()` | `System.getProperty("user.dir")` |
| `os.environ` | `System.getenv()` |

### 19.10 `PROJECT_ROOT` 路径计算：开发 vs 打包部署

本项目用 `Path(__file__).parent` 链式计算项目根目录：

```python
# infrastructure/config.py
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
# __file__ = .../space-aiagent-v1/src/space_aiagent/infrastructure/config.py
# .parent × 4 = .../space-aiagent-v1/
```

这个写法在**开发环境**完全正确，但有人会担心：**`pip install .` 打包后目录结构变了，这还能用吗？**

#### 开发环境 vs 打包安装后的目录结构

```
开发环境（pip install -e . 或直接运行源码）:
/Users/dev/space-aiagent-v1/          ← PROJECT_ROOT
├── src/space_aiagent/infrastructure/config.py    ← __file__
├── config/application.yaml
├── .env
└── data/
→ Path(__file__).parent × 4 = /Users/dev/space-aiagent-v1/  ✅ 正确

打包安装后（pip install .）:
/opt/python/lib/python3.13/site-packages/    ← 这不是项目根目录！
├── space_aiagent/infrastructure/config.py   ← __file__
├── space_aiagent/prompts/orchestrator.md
└── ...
→ Path(__file__).parent × 4 = /opt/python/lib/python3.13/   ❌ 错误
```

#### 但在企业实践中，这个问题几乎不存在

原因：**Web 服务不走 `pip install .` 部署**。企业 Python Web 服务的实际部署方式：

```
┌──────────────────────────────────────────────────────┐
│  Docker + 源码 COPY（最主流，60-70%）                   │
│  COPY src/ /app/src/                                  │
│  COPY config/ /app/config/                            │
│  CMD ["python", "-m", "space_aiagent.main"]           │
│  → 目录结构和开发环境完全一致，Path(__file__) 依然正确    │
├──────────────────────────────────────────────────────┤
│  虚拟环境 + Git 拉取源码（20-30%）                       │
│  git pull && source venv/bin/activate && python -m xxx │
│  → 源码就在项目根目录下，Path(__file__) 依然正确         │
├──────────────────────────────────────────────────────┤
│  pip install . 部署 Web 服务（<5%）                     │
│  → 很少有人这么干，Web 服务不是"分发给第三方的库"         │
└──────────────────────────────────────────────────────┘
```

**关键认识**：`pip install .` 打包分发是给**库/SDK**用的（别人 `import` 你的包）。Web 服务部署是**自己运行自己的代码**，直接 COPY 源码更简单、更透明。

#### Docker 部署的典型 Dockerfile

```dockerfile
FROM python:3.13
WORKDIR /app

# 只装依赖（不 pip install 项目本身）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 源码直接拷贝（目录结构和开发环境一致）
COPY src/ ./src/
COPY config/ ./config/

# 直接运行源码
CMD ["python", "-m", "space_aiagent.main"]
```

容器内的目录结构：

```
/app/                              ← PROJECT_ROOT（和开发环境一样）
├── src/space_aiagent/...          ← Path(__file__) 找得到
├── config/application.yaml        ← 找得到
├── config/knowledge/AGENTS.md     ← 找得到
└── .env                           ← 找得到
```

#### 最佳实践：环境变量兜底

虽然 Web 服务部署中 `Path(__file__)` 不会出问题，但严谨的企业做法会加一层环境变量兜底：

```python
import os
from pathlib import Path

# 优先用环境变量（生产环境由 Docker/systemd 设置）
# 回退到 __file__ 计算（开发环境 editable install）
PROJECT_ROOT = (
    Path(os.environ["PROJECT_ROOT"])
    if "PROJECT_ROOT" in os.environ
    else Path(__file__).parent.parent.parent.parent
)
```

```dockerfile
# Dockerfile 中设置
ENV PROJECT_ROOT=/app
```

```ini
# systemd service 文件中设置
[Service]
Environment="PROJECT_ROOT=/opt/space-aiagent"
```

**注意不能把 `PROJECT_ROOT` 放在 `.env` 里**——因为读 `.env` 需要 `PROJECT_ROOT`，形成了循环依赖。必须用系统级环境变量。

#### 总结

| 场景 | Path(__file__) 正确？ | 说明 |
|------|:---:|------|
| 开发（`pip install -e .`） | ✅ | 源码在原位，editable 模式 |
| Docker COPY 源码部署 | ✅ | 目录结构和开发环境一致 |
| Git + venv 部署 | ✅ | 同上 |
| `pip install .` 部署 | ❌ | 但企业几乎不用这种方式部署 Web 服务 |
| 环境变量兜底 | ✅ | 最严谨，所有场景都覆盖 |

**一句话结论**：`Path(__file__).parent` 链式计算在企业 Web 服务部署中**实际上不会出问题**，加环境变量兜底是锦上添花的最佳实践。

---

## 20. Python 包机制：`__init__.py` 与模块导入

### 20.1 `__init__.py` 的三个作用

#### 作用一：标记目录为 Python 包

Python 用 `__init__.py` 的存在来判断一个目录是不是包（package）。没有这个文件，`import` 找不到里面的模块。

```
src/space_aiagent/
├── __init__.py              ← 有这个文件，Python 才认为这是"包"
├── api/
│   ├── __init__.py          ← 同上
│   ├── routes.py
│   └── websocket.py
├── bridge/
│   ├── __init__.py
│   ├── ws_bridge.py
│   ├── session.py
│   └── response_renderer.py
└── skills/
    ├── __init__.py
    ├── registry.py
    └── scene_management/
        ├── __init__.py      ← 空文件，纯标记作用
        └── tools.py
```

本项目中有 7 个 `__init__.py` 是空文件（纯标记），5 个有实际内容。

> **Python 3.3+ 的隐式命名空间包**允许没有 `__init__.py` 也能导入，但显式创建是最佳实践——避免导入路径歧义和工具兼容性问题。

#### 作用二：简化导入路径（re-export）

`__init__.py` 可以重新导出子模块的符号，让使用者少写路径。

**本项目实际例子**：

```python
# bridge/__init__.py 的实际内容
from contextvars import ContextVar

from .session import SessionManager       # 从子模块导入
from .ws_bridge import WSBridge           # 从子模块导入

__all__ = ["SessionManager", "WSBridge", "bridge_var"]

bridge_var: ContextVar[WSBridge | None] = ContextVar("bridge_var", default=None)
```

**效果**：

```python
# 没有 __init__.py 的 re-export，必须写全路径
from space_aiagent.bridge.ws_bridge import WSBridge
from space_aiagent.bridge.session import SessionManager
from space_aiagent.bridge import bridge_var  # 这个只能从 __init__.py 导入

# 有了 __init__.py 的 re-export，可以简写
from space_aiagent.bridge import WSBridge, SessionManager, bridge_var
```

本项目 `websocket.py` 中就是用简写形式：

```python
# websocket.py 第 29 行
from space_aiagent.bridge import SessionManager, bridge_var
# 而不是：
# from space_aiagent.bridge.session import SessionManager
# from space_aiagent.bridge import bridge_var
```

#### 作用三：`__all__` 控制公开 API

```python
# bridge/__init__.py
__all__ = ["SessionManager", "WSBridge", "bridge_var"]
```

`__all__` 有两个作用：

```python
# 1. 控制 from xxx import * 能导入什么
from space_aiagent.bridge import *
# 只会导入 SessionManager, WSBridge, bridge_var
# 不会导入其他子模块中的东西

# 2. 文档作用：告诉 IDE 和开发者"这是包的公开 API"
# 不在 __all__ 里的，视为内部实现
```

**本项目中 `__all__` 的使用情况**：

| 包 | `__all__` 内容 |
|----|--------------|
| `space_aiagent` | 无（只有 `__version__`） |
| `bridge` | `["SessionManager", "WSBridge", "bridge_var"]` |
| `skills` | `["SkillLoader", "SkillRegistry"]` |
| `middleware` | `["LoggingMiddleware"]` |
| `agents` | 无（只有 docstring） |
| `api` | 无（空文件） |

### 20.2 `__init__.py` 不会做的事情

**`__init__.py` 不是"导出机制"**——不放到 `__init__.py` 里的模块级变量也能被外部访问：

```python
# bridge/ws_bridge.py
class WSBridge:
    ...

# 外部可以直接 import，不需要 __init__.py 中转
from space_aiagent.bridge.ws_bridge import WSBridge  # ✅ 始终可以
```

`__init__.py` 的 re-export 只是**便捷别名**，不是唯一的访问途径。

### 20.3 包级变量和 `__version__`

```python
# space_aiagent/__init__.py 的实际内容
"""space-aiagent: 航天分析平台智能助手"""
__version__ = "0.1.0"

# 外部可以访问版本号
import space_aiagent
print(space_aiagent.__version__)  # "0.1.0"
```

这是 Python 社区的标准做法——在顶层 `__init__.py` 中声明 `__version__`。

**Java 对比**：

| Python | Java |
|--------|------|
| `__init__.py` | `package-info.java`（但作用不同） |
| `from xxx import Y` | `import xxx.Y` |
| `__all__` | `module-info.java` 的 `exports` |
| `__version__` | `MANIFEST.MF` 或 `pom.xml` 版本 |

### 20.4 导入的完整规则

```python
# 1. 导入模块
import space_aiagent.bridge.ws_bridge                    # 使用: space_aiagent.bridge.ws_bridge.WSBridge

# 2. 导入模块并起别名
import space_aiagent.bridge.ws_bridge as ws              # 使用: ws.WSBridge

# 3. 从模块导入特定符号
from space_aiagent.bridge.ws_bridge import WSBridge      # 使用: WSBridge

# 4. 从包导入（走 __init__.py 的 re-export）
from space_aiagent.bridge import WSBridge                # 等价于上面，但经过 __init__.py

# 5. 相对导入（在包内部使用）
from .session import SessionManager                      # . = 当前包目录
from ..models import EntityType                          # .. = 上一层包目录
```

---

## 21. Python 魔术方法（Dunder Methods）

### 21.1 什么是魔术方法

Python 中以双下划线开头和结尾的方法叫**魔术方法**（magic methods），也叫 **dunder methods**（double underscore 的缩写）。它们是 Python 实现**运算符重载、协议、钩子**的机制。

```
__init__   __str__   __repr__   __eq__   __len__   __getitem__
__name__   __file__  __all__    __version__  __main__
```

**Java 对比**：Java 中的 `toString()`、`equals()`、`hashCode()` 是方法命名约定；Python 的 `__str__`、`__eq__`、`__hash__` 是语言级协议，**由 Python 解释器在特定时机自动调用**。

### 21.2 `__init__` — 构造函数

`__init__` 是实例初始化方法，在 `MyClass()` 创建对象后自动调用。类似 Java 的构造函数。

```python
# 本项目中的实际用法（bridge/ws_bridge.py）
class WSBridge:
    def __init__(self, websocket: WebSocket, thread_id: str) -> None:
        self._ws = websocket
        self._thread_id = thread_id
        self._pending: dict[str, asyncio.Future] = {}

# 使用
bridge = WSBridge(websocket, "thread-123")   # 自动调用 __init__
```

```python
# 继承时调用父类 __init__
# 本项目中的用法（middleware/logging.py）
class LoggingMiddleware(AgentMiddleware):
    def __init__(self, thread_id: str = "") -> None:
        super().__init__()            # 调用父类 __init__（类似 Java 的 super()）
        self.thread_id = thread_id
        self.step_count = 0
```

**注意**：`__init__` 不是"创建对象"，而是"初始化对象"。真正创建对象的是 `__new__`（几乎不需要重写）。

### 21.3 `__str__` 和 `__repr__` — 字符串表示

```python
class ScenarioConfig:
    """场景配置"""
    def __init__(self, name: str, central_body: str = "Earth"):
        self.name = name
        self.central_body = central_body

    def __str__(self) -> str:
        """用户看到的友好文本（print() / str() 时调用）"""
        return f"场景: {self.name}（中心天体: {self.central_body}）"

    def __repr__(self) -> str:
        """开发者看到的调试文本（repr() / 在交互式终端直接输入变量名时调用）"""
        return f"ScenarioConfig(name={self.name!r}, central_body={self.central_body!r})"


config = ScenarioConfig("测试场景")

print(config)           # → "场景: 测试场景（中心天体: Earth）"          ← __str__
print(str(config))      # → 同上
print(repr(config))     # → "ScenarioConfig(name='测试场景', central_body='Earth')"  ← __repr__
```

**规则**：

| 方法 | 触发场景 | 目标读者 | 要求 |
|------|---------|---------|------|
| `__str__` | `print()` / `str()` / f-string | 终端用户 | 可读性优先 |
| `__repr__` | `repr()` / 日志 / 调试器 | 开发者 | 应能 `eval(repr(obj))` 重建对象 |

**不定义时的默认行为**：

```python
class Foo:
    pass

f = Foo()
print(f)     # → "<__main__.Foo object at 0x10a2b3c40>"    ← 默认 __str__ 不好看
```

**Java 对比**：`__str__` ≈ `toString()`，`__repr__` 没有直接对应（调试时的 toString）。

### 21.4 `__eq__` 和 `__hash__` — 相等性和哈希

```python
class Point:
    """二维坐标点"""
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def __eq__(self, other: object) -> bool:
        """定义 == 运算符的行为（类似 Java 的 equals()）"""
        if not isinstance(other, Point):
            return NotImplemented
        return self.x == other.x and self.y == other.y

    def __hash__(self) -> int:
        """定义哈希值（类似 Java 的 hashCode()）
        只有定义了 __eq__ 的对象才能放进 set / 当 dict 的 key
        """
        return hash((self.x, self.y))


p1 = Point(1.0, 2.0)
p2 = Point(1.0, 2.0)
p3 = Point(3.0, 4.0)

print(p1 == p2)         # True  ← 调用 __eq__
print(p1 == p3)         # False
print(p1 is p2)         # False ← is 比较的是对象身份（内存地址），不受 __eq__ 影响

# 没定义 __hash__ 时，Point 对象不能放进 set
points = {p1, p2, p3}   # 只有 2 个元素（p1 == p2，去重了）
```

**关键规则**（和 Java 完全一样）：
- 重写 `__eq__` 必须同时重写 `__hash__`
- 相等的对象必须有相同的哈希值
- `dataclass` 自动生成 `__eq__`，设置 `frozen=True` 时自动生成 `__hash__`

### 21.5 `__len__`、`__getitem__`、`__contains__` — 容器协议

```python
class EntityCollection:
    """实体集合（演示容器协议）"""
    def __init__(self):
        self._entities: list[dict] = []

    def add(self, entity: dict) -> None:
        self._entities.append(entity)

    def __len__(self) -> int:
        """len() 时调用"""
        return len(self._entities)

    def __getitem__(self, index: int) -> dict:
        """obj[index] 时调用（支持下标访问和切片）"""
        return self._entities[index]

    def __contains__(self, item: dict) -> bool:
        """in 运算符时调用"""
        return item in self._entities

    def __iter__(self):
        """for 循环时调用（返回迭代器）"""
        return iter(self._entities)


entities = EntityCollection()
entities.add({"name": "卫星A", "type": "satellite"})
entities.add({"name": "地面站1", "type": "ground"})

len(entities)                    # 2         ← __len__
entities[0]                      # {"name": "卫星A", ...}  ← __getitem__
{"name": "卫星A"} in entities    # True      ← __contains__
for e in entities:               # ...       ← __iter__
    print(e["name"])
```

**Java 对比**：

| Python | Java |
|--------|------|
| `__len__` | `size()` |
| `__getitem__(i)` | `get(i)` |
| `__contains__(x)` | `contains(x)` |
| `__iter__` | 实现 `Iterable<T>` 接口 |

### 21.6 `__missing__` — 字典键缺失钩子

本项目唯一一个自定义的非 `__init__` 的魔术方法，配合 `str.format_map` 用于模板渲染。

#### `str.format_map` 是什么

`str.format_map(mapping)` 是 Python 字符串的内置方法，用字典填充模板占位符：

```python
template = "场景 {name} 共有 {count} 个实体"
template.format_map({"name": "测试场景", "count": 3})
# → "场景 测试场景 共有 3 个实体"
```

它和 `template.format(**d)` 几乎等价，区别在于：

- `format(**d)` 先把字典**解包成关键字参数**，大字典有额外开销；并且**只接受真实 dict**，传子类时参数会被解包，子类特征丢失
- `format_map(d)` 直接把字典对象交给 CPython 内部遍历，更高效；并且**完整支持 dict 子类**——这是关键，让我们能用 `__missing__` 钩子兜底缺失键

#### `_SafeDict` 让缺失键不报错

直接用普通 dict，缺失键会抛 `KeyError`：

```python
template = "场景 {name} 共有 {count} 个实体"
template.format_map({"name": "测试场景"})    # ❌ KeyError: 'count'
```

`_SafeDict` 继承 dict 并重写 `__missing__`，缺失键时返回 `{key}` 占位符文本而非抛异常：

```python
# bridge/response_renderer.py 的实际代码
class _SafeDict(dict):
    """安全的格式化字典，缺失键时返回占位符而非抛出 KeyError"""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# 普通字典：键不存在 → KeyError
normal = {"name": "测试场景"}
normal["name"]           # "测试场景"
normal["count"]          # ❌ KeyError: 'count'

# _SafeDict：键不存在 → 返回占位符
safe = _SafeDict({"name": "测试场景"})
safe["name"]             # "测试场景"
safe["count"]            # "{count}"  ← 不报错，返回占位符

# 用在模板渲染中
template = "场景 {name} 共有 {count} 个实体"
result = template.format_map(_SafeDict({"name": "测试场景"}))
# → "场景 测试场景 共有 {count} 个实体"
# count 没传也不报错，保留了占位符
```

#### `f"{{{key}}}"` 三重花括号解决了什么

`__missing__` 返回值必须是字符串。我们想返回"未填充的占位符原文"——即字符串 `{count}`（带花括号），而不是变量值，也不是去掉花括号的 `count`。要把 `{key}` 当**字面文本**输出，而不是当 f-string 占位符**求值**，就需要三重花括号：

```python
key = "count"

f"{key}"        # → "count"        ← 单层：{key} 是占位符，求值成变量值
f"{{key}}"      # → "{key}"        ← 双层：{{ }} 转义成字面花括号，里面 "key" 是字面三字母
f"{{{key}}}"    # → "{count}"      ← 三层：外层 {{ }} 转义成字面 { }，内层 {key} 当占位符求值

# 双层和三层的区别：
# - 双层 {{ key }} 里的 "key" 是字面字符串，永远不会被替换
# - 三层 {{ {key} }} 把变量 key 的值插到字面花括号中间，得到 "{count}"
```

如果错写成 `return f"{{key}}"`（双层），无论字典缺哪个键都返回字面 `{key}`，模板里所有未填充占位符都会变成同一个 `{key}`，渲染结果完全错乱：

```python
# 错误写法
def __missing__(self, key): return f"{{key}}"   # 永远返回字面 "{key}"

template = "{name} 共有 {count} 个，{entity} 类型"
template.format_map(_SafeDict({}))
# → "{key} 共有 {key} 个，{key} 类型"   ← 三处都成了 "{key}"，无法定位缺失字段
```

正确写法 `f"{{{key}}}"` 让每个缺失字段返回自己的 `{count}`/`{name}`/`{entity}`，前端能立刻看出模板期望什么字段，便于排查 LLM 漏填了哪个 details。

#### 业务上解决了什么问题

`AgentResponse` 由 LLM 生成，`details` 字段是 LLM 自由填的 dict，无法保证一定包含模板里所有占位符。比如 `SCENE_CREATED` 模板需要 `{sceneName}`，但 LLM 偶尔可能输出空 `details`。`_SafeDict` 让渲染器保留 `{sceneName}` 占位符而不崩溃——比抛异常崩掉整个回复好得多，前端至少能看到大部分文案，知道是"场景已创建"这件事。

`tests/test_response_render.py:106` 的 `test_render_falls_back_to_summary_when_template_format_fails` 进一步验证了双层保险：即使 `format_map` 本身抛异常（测试中用 `type()` 动态构造一个 `format_map` 抛 `ValueError` 的伪模板对象注入 `renderer._templates`），`render()` 也会被 `try/except` 兜住，降级用 `summary` 文本回复。

```python
# tests/test_response_render.py:100-114（节选）
broken_template = type(
    "BadTemplate",
    (),
    {"format_map": lambda self, d: (_ for _ in ()).throw(ValueError("boom"))},
)()
monkeypatch.setitem(renderer._templates, ("success", "SCENE_CREATED"), broken_template)

result = renderer.render(response)
assert result == "降级文案"   # format_map 抛异常 → 走 except 分支，返回 summary
```

### 21.7 `__call__` — 可调用对象

```python
class RateLimiter:
    """简单的速率限制器"""
    def __init__(self, max_calls: int, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window = window_seconds
        self._timestamps: list[float] = []

    def __call__(self) -> bool:
        """让实例像函数一样被调用: limiter()"""
        import time
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < self.window]

        if len(self._timestamps) >= self.max_calls:
            return False    # 超限

        self._timestamps.append(now)
        return True         # 允许


# 使用：实例像函数一样调用
limiter = RateLimiter(max_calls=5)
limiter()    # True   ← 调用 __call__
limiter()    # True
# ...第 6 次
limiter()    # False  ← 超限
```

**Java 对比**：Java 没有直接对应。最接近的是实现 `FunctionalInterface`（如 `Supplier<T>`），但调用方式是 `limiter.get()` 而非 `limiter()`。

### 21.8 `__enter__` 和 `__exit__` — 上下文管理器

```python
class DatabaseConnection:
    """演示上下文管理器（with 语句）"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None

    def __enter__(self):
        """进入 with 块时调用"""
        import aiosqlite
        self._conn = aiosqlite.connect(self.db_path)    # 获取资源
        return self._conn                                 # as 变量接收

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 块时调用（即使有异常也会执行）"""
        if self._conn:
            self._conn.close()                            # 释放资源
        return False   # False = 异常继续传播；True = 吞掉异常


# 使用
with DatabaseConnection("./data/db.sqlite") as conn:
    conn.execute("SELECT * FROM users")
# ← 退出 with 块后自动关闭连接，即使上面出了异常
```

**最常见的使用场景**：文件操作、数据库连接、锁的获取/释放。

```python
# 内置的 open() 就实现了上下文管理器
with open("config.yaml") as f:
    content = f.read()
# f 自动关闭

# 等价于 Java 的 try-with-resources
# try (Connection conn = dataSource.getConnection()) { ... }
```

### 21.9 `__name__` — 模块身份标识

`__name__` 是 Python 内置的模块级变量，表示当前模块的名字。

**用途一：日志命名**

本项目所有模块都用 `__name__` 作为 logger 名称：

```python
# 每个 .py 文件都有这行
import logging
logger = logging.getLogger(__name__)

# 在不同文件中，__name__ 的值不同：
# api/websocket.py 中   → __name__ = "space_aiagent.api.websocket"
# bridge/ws_bridge.py 中 → __name__ = "space_aiagent.bridge.ws_bridge"
# skills/registry.py 中  → __name__ = "space_aiagent.skills.registry"
```

这样日志就能按模块名过滤：

```python
# 只看 bridge 模块的日志
logging.getLogger("space_aiagent.bridge").setLevel(logging.DEBUG)
```

**用途二：入口判断**

```python
# main.py 和 cli.py 的末尾都有：
if __name__ == "__main__":
    main()

# 当直接运行 python -m space_aiagent.main 时：
#   __name__ == "__main__"    → 执行 main()
# 当被其他模块 import 时：
#   __name__ == "space_aiagent.main"    → 不执行 main()
```

**Java 对比**：

```python
# Python
if __name__ == "__main__":
    main()

// Java
// public static void main(String[] args) {
//     Application.main(args);
// }
```

### 21.10 `__file__` — 当前文件路径

`__file__` 是模块级变量，保存当前 `.py` 文件的路径。本项目大量使用它来定位配置和资源文件。

```python
# 本项目中的实际用法

# infrastructure/config.py — 定位项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
# __file__ = .../src/space_aiagent/infrastructure/config.py
# .parent × 4 = 项目根目录

# agents/orchestrator.py — 定位提示词目录
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
# __file__ = .../src/space_aiagent/agents/orchestrator.py
# .parent.parent = .../src/space_aiagent/
# / "prompts" = .../src/space_aiagent/prompts/

# skills/registry.py — 定位 skills 目录
SKILLS_DIR = Path(__file__).parent
# __file__ = .../src/space_aiagent/skills/registry.py
# .parent = .../src/space_aiagent/skills/
```

**为什么不用 `os.getcwd()`？** 详见第 19.2 节——`os.getcwd()` 取决于启动目录，而 `__file__` 始终指向代码文件本身的物理位置。

### 21.11 `__all__` — 控制模块公开 API

除了在 `__init__.py` 中使用（见 20.1 节），`__all__` 也可以在普通模块中使用：

```python
# skills/registry.py
class SkillRegistry:
    ...     # 公开类

class _InternalHelper:
    ...     # 内部类（下划线前缀表示私有）

__all__ = ["SkillRegistry"]   # 只有 SkillRegistry 是公开 API


# 使用者
from space_aiagent.skills.registry import *
# 只导入 SkillRegistry，不导入 _InternalHelper
```

### 21.12 魔术方法速查表

| 分类 | 方法 | 触发时机 | Java 对应 |
|------|------|---------|----------|
| **对象生命周期** | `__init__` | `MyClass()` | 构造函数 |
| | `__new__` | 创建实例前（少见） | — |
| | `__del__` | 垃圾回收时 | `finalize()` |
| **字符串表示** | `__str__` | `print(obj)` / `str(obj)` | `toString()` |
| | `__repr__` | `repr(obj)` / 调试器 | 调试用 `toString()` |
| **比较运算** | `__eq__` | `a == b` | `equals()` |
| | `__lt__` | `a < b` | `Comparable.compareTo()` |
| | `__hash__` | `hash(obj)` / 放进 set | `hashCode()` |
| | `__bool__` | `if obj:` | — |
| **容器协议** | `__len__` | `len(obj)` | `size()` |
| | `__getitem__` | `obj[key]` | `get(key)` |
| | `__setitem__` | `obj[key] = val` | `put(key, val)` |
| | `__contains__` | `x in obj` | `contains(x)` |
| | `__iter__` | `for x in obj` | `Iterable<T>` |
| | `__missing__` | dict[key] 键不存在 | — |
| **可调用** | `__call__` | `obj()` | `FunctionalInterface` |
| **上下文管理** | `__enter__` | `with obj:` 进入 | try-with-resources |
| | `__exit__` | `with obj:` 退出 | try-with-resources |
| **运算符** | `__add__` | `a + b` | — |
| | `__mul__` | `a * b` | — |
| **模块级变量** | `__name__` | 模块名 | — |
| | `__file__` | 文件路径 | — |
| | `__all__` | `import *` 导出列表 | `module-info.java` |
| | `__version__` | 版本号（约定） | `MANIFEST.MF` |

### 21.13 什么时候该写魔术方法

| 场景 | 推荐方式 | 说明 |
|------|---------|------|
| 只需要 `__init__` + 几个普通方法 | 手写 class | 最常见的情况 |
| 需要 `__init__` + `__eq__` + `__repr__` | 用 `@dataclass` | 自动生成这三个方法 |
| 需要 `__hash__` | `@dataclass(frozen=True)` | 不可变 dataclass 自动生成 |
| 需要完整的容器行为 | 继承 `collections.UserDict` / `UserList` | 比直接继承 `dict`/`list` 更安全 |
| 需要 `__call__` | 考虑是否普通函数就够了 | 别为了"像函数"而写 class |

**dataclass 自动生成的魔术方法**：

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
    # 自动生成：__init__, __repr__, __eq__

p1 = Point(1.0, 2.0)
p2 = Point(1.0, 2.0)

print(p1)       # "Point(x=1.0, y=2.0)"     ← 自动 __repr__
print(p1 == p2) # True                       ← 自动 __eq__
```

---

## 22. 动态加载与反射机制（Skill 案例剖析）

> 本章以 `skills/registry.py` + `skills/loader.py` 为案例，把动态加载、反射、`Path`、`yaml`、`list`/`dict`、`importlib` 串成一条完整的知识链路。这些机制是后续做插件化、扩展点、Cython 兼容设计的基础。

### 22.1 整体设计与架构图

**业务目标**：Agent 不在编译期绑定所有工具，而是运行时根据 `subagents.yaml` 的配置决定加载哪几个 Skill 的工具。新增 Skill 只需在 `skills/` 下放一个目录 + 一个 `skill.yaml` + 一个 `tools.py`，零代码改动。

**关键链路**：

```
                ┌────────────────────────────┐
                │  config/subagents.yaml     │
                │  ─────────────────────────  │
                │  agents:                   │
                │    - name: scene-agent     │
                │      skills:               │
                │        - scene_management  │
                │    - name: entity-agent    │
                │      skills:               │
                │        - entity_management │
                │        - orbit_management  │
                └─────────────┬──────────────┘
                              │ read_text + yaml.safe_load
                              ▼
                ┌────────────────────────────┐
                │   agents/subagents.py      │
                │   load_subagents(loader)   │
                └─────────────┬──────────────┘
                              │ for each agent_cfg:
                              │   loader.load_skills(cfg["skills"])
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │             skills/loader.py  (SkillLoader)              │
   │   ┌────────────────────────────────────────────────┐     │
   │   │  load_skill(name):                             │     │
   │   │    1. 查缓存 (_loaded)        ← dict          │     │
   │   │    2. registry.get_skill(name)                 │     │
   │   │    3. _import_tools_module(skill_dir, name)    │     │
   │   │    4. _extract_tools(module)   ← dir/getattr  │     │
   │   │    5. 写缓存并返回              ← list         │     │
   │   └────────────────────────────────────────────────┘     │
   └─────┬────────────────────────────────────┬───────────────┘
         │                                    │
         ▼                                    ▼
   ┌───────────────────┐              ┌─────────────────────┐
   │ skills/registry   │              │  importlib.util     │
   │  .py              │              │  spec_from_file_    │
   │                   │              │  location()         │
   │ SkillRegistry     │              │  module_from_spec() │
   │  .discover()      │              │  exec_module()      │
   │  .get_skill(name) │              └─────────────────────┘
   │                   │
   │ SkillInfo         │
   │  .name            │
   │  .skill_dir       │
   │  .description     │
   │  .triggers        │
   └─────────┬─────────┘
             │ discover() 扫描
             ▼
   ┌─────────────────────────────────────────────┐
   │  src/space_aiagent/skills/                  │
   │  ├── scene_management/                      │
   │  │   ├── skill.yaml        ← 元信息         │
   │  │   └── tools.py          ← @tool 函数     │
   │  ├── entity_management/                     │
   │  │   ├── skill.yaml                         │
   │  │   └── tools.py                           │
   │  └── orbit_management/                      │
   │      ├── skill.yaml                         │
   │      └── tools.py                           │
   └─────────────────────────────────────────────┘
```

**调用时序**（首次加载 `scene_management`）：

```
subagents.py          loader.py              registry.py          importlib           tools.py
     │                     │                       │                   │                   │
     │ load_skills([...])  │                       │                   │                   │
     ├────────────────────>│                       │                   │                   │
     │                     │ get_skill(name)       │                   │                   │
     │                     ├──────────────────────>│                   │                   │
     │                     │<────── SkillInfo ─────┤                   │                   │
     │                     │                       │                   │                   │
     │                     │ _import_tools_module  │                   │                   │
     │                     ├──────────────────────────────────────────>│                   │
     │                     │                       │   spec_from_file_location(tools.py)    │
     │                     │                       │   module_from_spec(spec)               │
     │                     │                       │   exec_module(module) ───────────────>│ 执行模块顶层代码
     │                     │                       │                   │                   │ @tool 装饰器运行
     │                     │<────── module ────────┼───────────────────┼───────────────────┤
     │                     │                       │                   │                   │
     │                     │ _extract_tools(module)│                   │                   │
     │                     │   dir(module)         │                   │                   │
     │                     │   getattr(...)        │                   │                   │
     │                     │   isinstance(BaseTool)│                   │                   │
     │                     │                       │                   │                   │
     │<── [tool, tool,...]─┤                       │                   │                   │
     │                     │                       │                   │                   │
```

**为什么这种设计有价值（即使后面会被简化掉，也值得学习）**：

| 设计目标 | 实现手段 | 价值 |
|---------|---------|------|
| 解耦：Agent 不感知具体工具 | YAML 配置 + 动态加载 | 新增 Skill 不改 Agent 代码 |
| 缓存：避免重复加载 | `_loaded: dict[str, list]` | 第二次调用 O(1) |
| 反射：不硬编码函数名 | `dir()` + `getattr()` + `isinstance()` | `tools.py` 新增工具，loader 零改动 |
| 元数据驱动：每个 Skill 自描述 | `skill.yaml` 提供描述、触发词 | 注册表可被 Orchestrator 提示词使用 |

---

### 22.2 `pathlib.Path` 常用方法

`Path` 是 Python 3 推荐的路径处理类，比 `os.path` 更面向对象、更易读。

#### 项目中用到的

`agents/subagents.py:48`：
```python
config_text = _SUBAGENTS_CONFIG.read_text(encoding="utf-8")
```

`skills/loader.py:77-78`：
```python
tools_path = skill_dir / "tools.py"
if not tools_path.exists():
    ...
```

#### 常用方法速查表

| 方法/属性 | 返回 | 用途 | 示例 |
|----------|------|------|------|
| `Path("a") / "b"` | `Path` | 拼接路径（自动处理分隔符） | `Path("src") / "main.py"` → `src/main.py` |
| `.parent` | `Path` | 父目录 | `Path("a/b/c.py").parent` → `a/b` |
| `.name` | `str` | 文件名（含扩展名） | `Path("a/b/c.py").name` → `c.py` |
| `.stem` | `str` | 文件名（不含扩展名） | `Path("a/b/c.py").stem` → `c` |
| `.suffix` | `str` | 扩展名 | `Path("a/b/c.py").suffix` → `.py` |
| `.parts` | `tuple` | 路径各段 | `Path("a/b/c.py").parts` → `('a','b','c.py')` |
| `.exists()` | `bool` | 是否存在 | `if not tools_path.exists():` |
| `.is_file()` | `bool` | 是否是文件 | |
| `.is_dir()` | `bool` | 是否是目录 | |
| `.read_text(encoding="utf-8")` | `str` | 一次性读全文 | `config.read_text()` |
| `.read_bytes()` | `bytes` | 读二进制 | 图片、压缩包 |
| `.write_text(s)` | `int` | 写文本（覆盖） | `p.write_text("hello")` |
| `.mkdir(parents=True, exist_ok=True)` | None | 创建目录 | 类似 `mkdir -p` |
| `.iterdir()` | `Iterator[Path]` | 列出目录项 | 不递归 |
| `.rglob("*.py")` | `Iterator[Path]` | 递归查找 | 类似 `find . -name "*.py"` |
| `.glob("*.py")` | `Iterator[Path]` | 非递归查找 | |
| `.resolve()` | `Path` | 转绝对路径（解析 `..`、软链） | |
| `.with_suffix(".pyd")` | `Path` | 替换扩展名 | `Path("a.py").with_suffix(".pyd")` → `a.pyd` |
| `.relative_to(base)` | `Path` | 求相对路径 | `Path("a/b").relative_to("a")` → `b` |

#### `read_text` vs `open(...).read()`

等价写法对比：

```python
# Path.read_text（推荐，简洁）
text = Path("config.yaml").read_text(encoding="utf-8")

# 传统 open（适合大文件流式读、按行读）
with open("config.yaml", "r", encoding="utf-8") as f:
    text = f.read()
    # 或逐行：for line in f: ...
```

`read_text` 内部就是 `open + read + close`，对小文件（配置、提示词）非常合适。**注意：一次读入内存，不要用于 GB 级文件。**

#### 拼接路径的两种风格

```python
# 风格 1：Path 运算符重载（推荐）
config_path = Path(__file__).parent.parent / "config" / "subagents.yaml"

# 风格 2：os.path.join（旧风格）
import os
config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "subagents.yaml"
)
```

`Path` 的 `/` 运算符是 `__truediv__` 魔术方法的语法糖，跨平台自动用 `/`（POSIX）或 `\`（Windows）。

#### Java 对比

| Python `Path` | Java `Path` / `File` |
|---------------|----------------------|
| `Path("a") / "b"` | `Paths.get("a", "b")` |
| `.exists()` | `Files.exists(path)` |
| `.read_text()` | `Files.readString(path)`（JDK 11+） |
| `.write_text(s)` | `Files.writeString(path, s)` |
| `.rglob("*.py")` | `Files.walk(...).filter(...)` |
| `.mkdir(parents=True, exist_ok=True)` | `Files.createDirectories(path)` |

---

### 22.3 YAML 与 `yaml.safe_load`

#### 项目中用到的

`agents/subagents.py:48-49`：
```python
config_text = _SUBAGENTS_CONFIG.read_text(encoding="utf-8")
config = yaml.safe_load(config_text)
```

#### `yaml.safe_load` vs `yaml.load` 的关键区别

```python
import yaml

# safe_load：只解析基础类型（dict/list/str/int/bool/None），拒绝任意 Python 对象
config = yaml.safe_load(text)         # ✅ 推荐用于配置文件

# load：若不加 Loader 参数会调用已废弃的默认 Loader，
# 允许 YAML 中的 !!python/object 标签实例化任意类，等同于"反序列化漏洞"
obj = yaml.load(text)                 # ❌ 危险，攻击者可执行任意代码
obj = yaml.load(text, Loader=yaml.SafeLoader)   # 等价于 safe_load
```

**安全原则**：处理任何不可信来源的 YAML（用户上传、网络、第三方配置），必须用 `safe_load`。本项目配置文件由开发者写、部署时本地化，但仍坚持用 `safe_load`，是好习惯。

#### YAML 语法要点（结合 `subagents.yaml`）

```yaml
# subagents.yaml
agents:                              # ← 顶层是一个 list（注意 "-"）
  - name: scene-agent                # ← 列表项是 dict（注意缩进 + 冒号空格）
    description: "场景管理子智能体"   # ← 字符串可加可不加引号，含特殊字符时必加
    prompt_file: "scene_agent.md"
    skills:                          # ← 嵌套 list
      - scene_management             # ← 简单字符串列表

  - name: entity-agent
    description: "实体与轨道管理子智能体"
    prompt_file: "entity_agent.md"
    skills:
      - entity_management
      - orbit_management
```

解析后等价于：

```python
{
    "agents": [
        {
            "name": "scene-agent",
            "description": "场景管理子智能体",
            "prompt_file": "scene_agent.md",
            "skills": ["scene_management"],
        },
        {
            "name": "entity-agent",
            "description": "实体与轨道管理子智能体",
            "prompt_file": "entity_agent.md",
            "skills": ["entity_management", "orbit_management"],
        },
    ]
}
```

#### YAML 关键语法点

| 语法 | 含义 | 注意 |
|------|------|------|
| 缩进（空格） | 层级关系 | **只能用空格，不能用 Tab**，同层必须对齐 |
| `key: value` | 字典项 | **冒号后必须有一个空格**，否则是字符串 |
| `- item` | 列表项 | 短横线后有空格 |
| `#` | 注释 | 行首或行尾 |
| `"..."` / `'...'` | 字符串 | 含 `: #` `!` 等特殊字符时必加引号 |
| `|` | 块字符串（保留换行） | 多行模板（提示词、脚本） |
| `>` | 折叠字符串（换行变空格） | 段落文本 |
| `null` / `~` | None | |
| `true` / `false` | bool | 也接受 `yes`/`no`，但易混淆，不推荐 |
| `&anchor` / `*anchor` | 锚点与引用 | 复用相同结构，避免重复 |
| `${VAR:default}` | **不是 YAML 原生语法** | 是本项目 `infrastructure/config.py` 自己实现的占位符替换 |

#### 多行字符串两种风格（写提示词时常用）

```yaml
# 块字符串：保留原始换行（system_prompt 模板常用）
system_prompt: |
  你是一个场景管理 Agent。
  规则：
  1. 创建实体前必须确保场景已创建
  2. 不要直接返回 JSON

# 折叠字符串：连续换行折叠成一个空格
description: >
  这是一个用于管理航天场景的智能助手，
  支持创建、重命名、删除等操作。
```

#### Java 对比

| Python `yaml.safe_load` | Java |
|------------------------|------|
| `yaml.safe_load(text)` | `new Yaml().load(text)`（SnakeYAML） |
| `yaml.safe_load` 默认拒绝任意类 | SnakeYAMD 默认允许，需要 `new SafeConstructor()` 显式限制 |
| 后续手动校验 | `@ConfigurationProperties` + `@Valid` |

SnakeYAML 历史上也有 CVE（CVE-2022-1471），与 `yaml.load` 默认不安全的逻辑完全一致。

---

### 22.4 `list` 与 `dict` 常用方法

#### 项目中用到的

`skills/loader.py:64-67`：
```python
tools: list[BaseTool] = []      # 创建空 list
for name in skill_names:
    tools.extend(self.load_skill(name))   # extend 批量追加
return tools
```

`skills/loader.py:71`：
```python
self._loaded.pop(skill_name, None)    # 安全删除（键不存在返回 None，不报错）
```

#### `list` 常用方法

| 方法 | 说明 | 时间复杂度 | 示例 |
|------|------|-----------|------|
| `lst.append(x)` | 末尾追加一个 | O(1) | `[1].append(2)` → `[1, 2]` |
| `lst.extend(iter)` | 末尾批量追加 | O(k) | `[1].extend([2,3])` → `[1, 2, 3]` |
| `lst.insert(i, x)` | 指定位置插入 | O(n) | `[1,3].insert(1, 2)` → `[1, 2, 3]` |
| `lst.pop()` | 弹出末尾 | O(1) | `[1,2].pop()` → 返回 `2`，list 变 `[1]` |
| `lst.pop(i)` | 弹出指定位置 | O(n) | `[1,2,3].pop(0)` → 返回 `1` |
| `lst.remove(x)` | 删除第一个等于 x 的 | O(n) | `[1,2,1].remove(1)` → `[2, 1]` |
| `lst.clear()` | 清空 | O(n) | |
| `lst.index(x)` | 查索引，不存在抛异常 | O(n) | `[1,2].index(2)` → `1` |
| `lst.count(x)` | 计数 | O(n) | `[1,1,2].count(1)` → `2` |
| `lst.sort(key=...)` | 原地排序 | O(n log n) | `[3,1,2].sort()` |
| `lst.reverse()` | 原地反转 | O(n) | |
| `lst.copy()` | 浅拷贝 | O(n) | |
| `x in lst` | 是否包含 | O(n) | `2 in [1,2]` → `True` |
| `lst[i:j]` | 切片（生成新 list） | O(k) | `[1,2,3][1:]` → `[2, 3]` |
| `len(lst)` | 长度 | O(1) | |
| `+` / `+=` | 拼接 | O(n+k) | `[1] + [2]` → `[1, 2]` |

**易混淆点**：`append` vs `extend`

```python
lst = [1, 2]
lst.append([3, 4])    # 把整个 list 当成一个元素
# lst → [1, 2, [3, 4]]   ← 嵌套了

lst = [1, 2]
lst.extend([3, 4])    # 把每个元素分别追加
# lst → [1, 2, 3, 4]
```

#### `dict` 常用方法

| 方法 | 说明 | 示例 |
|------|------|------|
| `d[k] = v` | 设置（已存在则覆盖） | `d["a"] = 1` |
| `d[k]` | 取值（不存在抛 `KeyError`） | `d["a"]` |
| `d.get(k, default)` | 安全取值（不存在返回 default） | `d.get("x", 0)` → `0` |
| `d.pop(k)` | 删除并返回（不存在抛异常） | `d.pop("a")` |
| `d.pop(k, default)` | 安全删除并返回 | `d.pop("x", None)` → `None` |
| `d.popitem()` | 弹出最后一项（LIFO，3.7+ 有序） | |
| `d.update(other)` | 批量合并 | `d.update({"x": 1})` |
| `d.keys()` / `d.values()` / `d.items()` | 视图（动态） | `for k, v in d.items():` |
| `d.setdefault(k, default)` | 不存在才设置，返回值 | `d.setdefault("c", [])` |
| `d.clear()` | 清空 | |
| `d.copy()` | 浅拷贝 | |
| `k in d` | 是否有 key（O(1)） | `"a" in d` |
| `del d[k]` | 删除（不存在抛异常） | `del d["a"]` |
| `len(d)` | 项数 | |
| `dict()` / `{}` | 构造 | `dict(a=1)` → `{"a": 1}` |

**项目中的常见用法**：

```python
# 1. 缓存命中检查（loader.py:43）
if skill_name in self._loaded:        # O(1) 查找
    return self._loaded[skill_name]

# 2. 安全删除（loader.py:71）
self._loaded.pop(skill_name, None)    # 即使没缓存也不报错

# 3. 遍历
for name, tools in self._loaded.items():
    print(name, len(tools))

# 4. 推导式构造
skill_map = {t.name: t for t in tools}    # 工具名 → 工具对象
```

#### `defaultdict` 与 `setdefault` 模式

当需要"按 key 分组收集"时，原生 `dict` 要先判断 key 是否存在：

```python
# 模式 1：setdefault（一次写入）
groups = {}
for tool in tools:
    groups.setdefault(tool.group, []).append(tool)

# 模式 2：defaultdict（更简洁）
from collections import defaultdict
groups = defaultdict(list)
for tool in tools:
    groups[tool.group].append(tool)    # 不存在自动建空 list
```

#### Java 对比

| Python | Java |
|--------|------|
| `list` | `ArrayList` |
| `dict` | `HashMap` / `LinkedHashMap`（3.7+ dict 保留插入顺序） |
| `lst.append(x)` | `lst.add(x)` |
| `lst.extend(iter)` | `lst.addAll(c)` |
| `d.get(k, default)` | `d.getOrDefault(k, default)` |
| `d.items()` | `d.entrySet()` |
| `defaultdict(list)` | `Map.computeIfAbsent(k, k -> new ArrayList<>())` |
| `k in d` | `d.containsKey(k)` |
| `[1,2] + [3]` | `Stream.concat(...)` 或 `addAll` |

---

### 22.5 `importlib` 详解：运行时加载模块

#### 项目中用到的（loader.py:82-91）

```python
import importlib.util

module_name = f"space_aiagent.skills.{skill_dir.name}.tools"
spec = importlib.util.spec_from_file_location(module_name, str(tools_path))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

这一段是"动态加载"的核心：**绕过 `import` 语句，从任意路径加载一个 `.py` 文件作为模块**。

#### 三步流程拆解

**Step 1：`spec_from_file_location(name, path)` — 创建"模块规格"**

```python
spec = importlib.util.spec_from_file_location(
    name="space_aiagent.skills.scene_management.tools",   # 模块全名
    location="/abs/path/to/skills/scene_management/tools.py",   # 文件路径
)
```

- **`name`**：模块在 `sys.modules` 中的键名，也是 `module.__name__` 的值。命名要符合包路径，便于日志、调试、`__name__ == "__main__"` 判断
- **`location`**：必须是可以解析到的真实文件路径
- 返回值：一个 `ModuleSpec` 对象，**此时还没读文件内容**

**Step 2：`module_from_spec(spec)` — 创建空模块对象**

```python
module = importlib.util.module_from_spec(spec)
```

- 根据 spec 创建一个空壳模块（设置了 `__name__`、`__file__`、`__loader__` 等属性）
- 此时模块体里的代码**还没执行**，模块内的函数/类都不存在

**Step 3：`spec.loader.exec_module(module)` — 执行模块顶层代码**

```python
spec.loader.exec_module(module)
```

- 等价于执行 `tools.py` 顶层的所有语句（导入、装饰器、函数定义）
- 装饰器在此刻运行：`@tool def create_scenario(...)` 把普通函数变成 `BaseTool` 实例并绑定到模块属性
- 执行完成后，`module.create_scenario`、`module.query_scenario` 等属性可用

#### 为什么不直接 `import`？

```python
# 直接 import：路径必须固定，编译期就确定
from space_aiagent.skills.scene_management.tools import create_scenario

# importlib：路径可以来自配置/参数，运行时决定
spec = importlib.util.spec_from_file_location(
    name=...,
    location=config["tool_module_path"]   # ← 来自 YAML、用户输入、扫描结果
)
```

**典型场景**：
- **插件系统**：扫描 `plugins/*.py`，让用户放新文件就能扩展功能（IDE、Jupyter 都用这套）
- **配置驱动加载**：根据 YAML 决定加载哪个实现（本项目）
- **避免循环导入**：在函数内部 `importlib.import_module` 延迟加载

#### 完整对比：`import` 语句、`import_module`、`spec_from_file_location`

| 方式 | 何时用 | 路径要求 |
|------|-------|---------|
| `import x.y.z` | 编译期就知道名字 | 必须在 `sys.path` 中能找到 |
| `importlib.import_module("x.y.z")` | 运行期才确定模块名 | 同上 |
| `importlib.util.spec_from_file_location(name, path)` | 运行期才确定**文件路径** | 任意路径，不需要在 `sys.path` |

#### 把模块注册到 `sys.modules`（防止重复加载）

上面三步**没有**把模块加进 `sys.modules`，意味着同一个文件被加载两次会生成两个独立模块对象。完整写法：

```python
import sys
import importlib.util

def load_module_from_path(name: str, path: str):
    if name in sys.modules:                  # 已经加载过，直接复用
        return sys.modules[name]

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module               # 先注册（防止循环导入时找不到）
    spec.loader.exec_module(module)
    return module
```

本项目 `loader.py` 用了 `_loaded` 缓存来去重，效果类似但更细粒度（按 skill 名而非模块全名）。

#### Java 对比

| Python `importlib` | Java |
|-------------------|------|
| `spec_from_file_location` | 无直接对应（Java 不允许"任意路径加载类文件"） |
| `module_from_spec` + `exec_module` | `ClassLoader.defineClass(byte[])` |
| `importlib.import_module("x.y.Z")` | `Class.forName("x.y.Z")` |
| `module.__dict__` | `Class.getFields()` / `getMethods()` |

Java 的类加载严格依赖 ClassLoader 层级和已编译的 `.class` 文件，Python 的动态性来自"模块即命名空间 + 运行时执行"。

---

### 22.6 `ModuleSpec` 是什么

`ModuleSpec` 是 Python 3.4+ 引入的"模块元信息容器"，是 import system 的核心抽象。

```python
spec = importlib.util.spec_from_file_location("my_module", "/path/to/file.py")

print(spec.name)           # "my_module"
print(spec.origin)         # "/path/to/file.py"  ← 文件路径
print(spec.loader)         # <SourceFileLoader ...>  ← 负责执行模块的加载器
print(spec.submodule_search_locations)  # None（普通模块）或 list（包）
print(spec.cached)         # ".pyc" 文件路径（如果存在）
print(spec.has_location()) # True（基于文件） / False（内置模块）
```

**关键属性**：

| 属性 | 类型 | 含义 |
|------|------|------|
| `name` | `str` | 模块全限定名 |
| `loader` | `Loader` | 实际执行加载的对象（`SourceFileLoader` / `ExtensionFileLoader` / 自定义） |
| `origin` | `str \| None` | 模块来源（文件路径 / URL / `<builtin>`） |
| `submodule_search_locations` | `list \| None` | 包的搜索路径（普通模块为 `None`，包为目录列表） |
| `cached` | `str \| None` | 编译缓存路径（`.pyc`） |
| `parent` | `str` | 父包名（Python 3.11+） |

**Loader 接口**（自定义加载器时实现）：

```python
class MyLoader:
    def create_module(self, spec):
        return None          # 返回 None 用默认 module 类
    def exec_module(self, module):
        # 把代码 exec 进 module.__dict__
        ...
```

#### 工作流程示意

```
import my_package.my_module
        │
        ▼
┌─────────────────────────────┐
│  sys.meta_path finders      │  ← 按顺序查找
│  ──────────────────────     │
│  1. BuiltinImporter         │  ← 内置模块（sys、os）
│  2. FrozenImporter          │  ← 冻结模块
│  3. PathFinder              │  ← 在 sys.path 中找
│      └─ 遍历 path finders   │
└────────────┬────────────────┘
             │ 返回 ModuleSpec（找不到返回 None）
             ▼
┌─────────────────────────────┐
│  Loader.create_module(spec) │  ← 创建模块对象
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│  Loader.exec_module(module) │  ← 执行模块代码
└────────────┬────────────────┘
             │
             ▼
   sys.modules["my_package.my_module"] = module
```

`spec_from_file_location` 本质是**跳过 finder 阶段**，直接构造一个 `ModuleSpec` 给你，省去了"在 sys.path 里查找"的步骤。

---

### 22.7 Python 反射机制（`dir` + `getattr`）

#### 项目中用到的（loader.py:96-107）

```python
def _extract_tools(self, module) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for attr_name in dir(module):               # 遍历模块所有属性名
        attr = getattr(module, attr_name)        # 按名取值
        if isinstance(attr, BaseTool):           # 类型筛选
            tools.append(attr)
    return tools
```

这段代码就是教科书式的反射应用：**不写死函数名，运行时动态发现模块里所有"工具"**。

#### 四个核心函数

| 函数 | 作用 | 示例 |
|------|------|------|
| `dir(obj)` | 返回对象所有属性/方法名的**列表**（含继承的） | `dir(module)` → `["__name__", "create_scenario", ...]` |
| `getattr(obj, name)` | 等价于 `obj.name`，但名字是字符串 | `getattr(module, "create_scenario")` |
| `getattr(obj, name, default)` | 不存在返回 default，不抛异常 | `getattr(obj, "xxx", None)` |
| `setattr(obj, name, value)` | 等价于 `obj.name = value` | `setattr(module, "x", 1)` |
| `hasattr(obj, name)` | 是否存在该属性 | `hasattr(module, "create_scenario")` |
| `delattr(obj, name)` | 删除属性 | `delattr(module, "x")` |

#### `dir(module)` 返回什么

模块的 `dir()` 包含：

1. 模块内显式定义的所有名字（函数、类、变量）
2. 通过 `import` 引入的名字
3. 模块自带的双下划线属性（`__name__`、`__file__`、`__doc__`、`__loader__` 等）

```python
# skills/scene_management/tools.py 内容：
# from langchain_core.tools import tool
# @tool
# def create_scenario(...): ...
# @tool
# def query_scenario(...): ...

import skills.scene_management.tools as m
print(dir(m))
# ['__builtins__', '__cached__', '__doc__', '__file__', '__loader__',
#  '__name__', '__package__', '__spec__', 'create_scenario',
#  'query_scenario', 'tool']            ↑ 我们关心的就这两个
```

`_extract_tools` 用 `isinstance(attr, BaseTool)` 过滤掉字符串、模块对象等无关项，只保留被 `@tool` 装饰过的函数。

#### `getattr` vs 直接访问

```python
# 直接访问：编译期必须知道属性名
fn = module.create_scenario

# getattr：属性名可以是变量、用户输入、字符串拼接
attr_name = "create_" + "scenario"
fn = getattr(module, attr_name)         # 完全等价

# getattr 安全版：属性不存在时给默认值
fn = getattr(module, "maybe_not_exist", None)
if fn is not None:
    fn()
```

#### `@tool` 装饰器与反射的配合

```python
# tools.py
from langchain_core.tools import tool

@tool
def create_scenario(name: str) -> str:
    """创建场景"""
    ...

# 装饰器等价于：
def create_scenario(name): ...
create_scenario = tool(create_scenario)
# 装饰后 create_scenario 变成了 BaseTool 实例（不再是普通函数）

# 所以 _extract_tools 里 isinstance 检查能命中
isinstance(create_scenario, BaseTool)   # True
```

#### 反射的典型应用场景

| 场景 | 实现 |
|------|------|
| **插件发现** | 扫描目录 → `importlib` 加载 → `dir()` + `isinstance` 找插件 |
| **序列化/反序列化** | 根据字符串类名 `getattr(module, ClassName)` 实例化对象 |
| **ORM 字段映射** | 数据库列名 → `getattr(model, column_name)` |
| **Mock 测试** | `setattr(obj, "method", fake_fn)` 替换方法 |
| **CLI 命令分发** | `command = sys.argv[1]; getattr(cli, command)()` |

---

### 22.8 Python 反射 vs Java 反射

#### 概念对照

| Python | Java | 说明 |
|--------|------|------|
| `dir(obj)` | `Class.getMethods()` / `getFields()` | 列出属性/方法 |
| `getattr(obj, name)` | `Field.get(obj)` / `Method.invoke(obj, args)` | 按名取值 |
| `setattr(obj, name, v)` | `Field.set(obj, v)` | 按名赋值 |
| `hasattr(obj, name)` | 反射 + 异常捕获 | 是否存在 |
| `type(obj)` | `obj.getClass()` | 获取类型 |
| `isinstance(obj, T)` | `T.class.isInstance(obj)` | 类型检查 |
| `importlib.import_module` | `Class.forName(name)` | 按名加载 |
| `module.__dict__` | 无直接对应（Java 类元数据封闭） | 命名空间字典 |

#### 代码对比：动态调用方法

**Python**：

```python
import importlib
module = importlib.import_module("skills.scene_management.tools")
fn = getattr(module, "create_scenario")    # 不需要类型转换
result = fn(name="测试")                    # 直接调用
```

**Java**：

```java
Class<?> clazz = Class.forName("com.example.SceneTools");
Object instance = clazz.getDeclaredConstructor().newInstance();
Method method = clazz.getMethod("createScenario", String.class);
Object result = method.invoke(instance, "测试");   // 返回 Object，需强转
```

#### 关键差异

| 维度 | Python | Java |
|------|--------|------|
| **访问控制** | 默认全部可访问（私有 `__x` 只是命名约定，仍能 `getattr`） | 必须显式 `setAccessible(true)` 才能访问 private |
| **类型安全** | 反射后直接调用，运行时才报错 | 返回 `Object`，需显式强转 |
| **性能** | 反射与直接访问差距小（一切都已是"按字典查属性"） | 反射比直接调用慢 10-100 倍 |
| **元对象** | 每个对象都有 `__class__`、`__dict__`，开放 | `Class` 是封闭的、需经反射 API 访问 |
| **设计哲学** | "我们都是成年人"（一切可见） | "封装是契约"（private 必须强制） |

#### Python 的"鸭子类型"减少反射需求

很多 Java 必须用反射的场景，Python 用鸭子类型或 `Protocol` 就够了：

```python
# Java 思路：反射调用任意具有 handle 方法的对象
public void dispatch(Object obj) {
    Method m = obj.getClass().getMethod("handle");
    m.invoke(obj);
}

# Python 思路：直接调用，"长得像就行"
def dispatch(obj):
    obj.handle()    # 任何有 handle 方法的对象都能传进来
```

只有当**属性名本身是动态的**（来自配置、用户输入），才必须用 `getattr`。

---

### 22.9 整体设计复盘：动态加载 vs 静态注册

本项目当前用动态加载，未来计划改为静态注册。两种思路对比：

| 维度 | 动态加载（当前） | 静态注册（计划） |
|------|---------------|-----------------|
| 新增 Skill | 加目录 + `skill.yaml` + `tools.py`，**零代码** | 改 `registry.py` 加一行 import + 一行字典 |
| 性能 | 每次启动多一次文件扫描 + 模块加载 | 启动即固定 |
| Cython 兼容 | ❌ `.py` → `.pyd` 后 `spec_from_file_location` 失败 | ✅ 标准导入即可 |
| 可调试性 | 反射发现，IDE 跳转困难 | 显式导入，IDE 全程可跳转 |
| 学习价值 | 高（涉及 importlib、反射、Path、yaml） | 低（一个普通 dict） |

**结论**：动态加载在 Skill 数量爆炸（几十上百个、由第三方贡献）时才真正发挥价值。本项目只有 3 个 Skill，用静态注册更简单、更安全、更利于编译保护。但作为学习材料，动态加载那套机制非常值得掌握。

---


| Java 概念 | Python 对应 |
|----------|------------|
| `@SpringBootApplication` | `FastAPI()` |
| `@RestController` | `APIRouter` + `@router.post` |
| `@ConfigurationProperties` | `pydantic-settings` |
| `application.yml` | `pyproject.toml` + `config/` |
| `CompletableFuture` | `asyncio.Future` / `async/await` |
| `ThreadLocal` | `ContextVar` |
| JPA Repository | `aiosqlite` + `AsyncSqliteSaver` |
| SLF4J + Logback | `structlog` + standard `logging` |
| Maven / Gradle | `pip` + `pyproject.toml` |
| Checkstyle / SpotBugs | `ruff` |
| JUnit + Mockito | `pytest` + `pytest-asyncio` |
| Lombok `@Data` | `dataclass` |
| `@Valid` | Pydantic `BaseModel` |

---

## 23. DeepAgents `context_schema` 源码解析

### 23.1 一句话结论

**DeepAgents 的 `context_schema` 完全委托给 LangChain 的 `create_agent`，再由 LangChain 透传给 LangGraph 的 `StateGraph`**。DeepAgents 自己几乎不做任何额外处理。

```
deepagents.create_deep_agent(context_schema=...)
        ↓ 透传
langchain.agents.create_agent(context_schema=...)
        ↓ 透传
langgraph.graph.StateGraph(context_schema=...)
        ↓ 注入到 runtime
langgraph.runtime.Runtime[ContextT]
```

源码证据（`deepagents/graph.py:506-508`）：

```python
context_schema: Schema class that defines immutable run-scoped context.

Passed through to [`create_agent`][langchain.agents.create_agent].
```

源码证据（`deepagents/graph.py:846`）：

```python
return create_agent(
    model=model,
    tools=all_tools,
    middleware=all_middleware,
    ...
    context_schema=context_schema,   # ← 原样透传
    checkpointer=checkpointer,
    ...
)
```

### 23.2 什么是 `context_schema`

`context_schema` 是 LangGraph v0.6.0 引入的**运行时不可变上下文**机制。它和 `state_schema` 是两条平行的数据通道：

| 维度 | `state_schema` | `context_schema` |
|------|---------------|------------------|
| 本质 | **可变状态**（Graph 内部流转） | **不可变运行时上下文**（运行前注入） |
| 修改方式 | 节点 return dict 触发 reducer 合并 | 节点内**只读**，运行期不可改 |
| 持久化 | 被 checkpointer 序列化 | 不被 checkpointer 持久化 |
| 用途 | messages、todos、字段累积 | user_id、tenant_id、db_conn 等依赖注入 |
| 类比 | Java 的 `HttpSession` | Spring 的 `@RequestScope Bean` / 函数参数 |

源码定义（`langgraph/runtime.py:124-201`，节选）：

```python
@dataclass
class Runtime(Generic[ContextT]):
    """注入到 graph 节点和 middleware 的运行时容器。"""
    context: ContextT = field(default=None)
    """静态运行时上下文，如 user_id、db_conn 等。可以视为'运行依赖'。"""

    store: BaseStore | None = field(default=None)
    stream_writer: StreamWriter = field(default=_no_op_stream_writer)
    ...
```

### 23.3 LangGraph 中的注入链路

`context_schema` 在底层走的是**函数参数注入**——LangGraph 通过签名解析，把 `context` 字段从 `Runtime` 里取出来注入到节点函数。

```python
# langgraph/runtime.py 的官方示例
from dataclasses import dataclass
from typing import TypedDict
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime


@dataclass
class Context:
    user_id: str


class State(TypedDict, total=False):
    response: str


def personalized_greeting(state: State, runtime: Runtime[Context]) -> State:
    """通过 runtime 参数拿到 context。"""
    user_id = runtime.context.user_id       # ← 类型安全的访问
    return {"response": f"Hello {user_id}"}


graph = (
    StateGraph(state_schema=State, context_schema=Context)   # ← 注册 schema
    .add_node("personalized_greeting", personalized_greeting)
    .set_entry_point("personalized_greeting")
    .set_finish_point("personalized_greeting")
    .compile()
)

# invoke 时传入 context 实例
result = graph.invoke({}, context=Context(user_id="user_123"))
```

**关键点**：
1. `context_schema` 是一个**类型**（`@dataclass` 或 `TypedDict`），不是实例
2. 实例在 `invoke(..., context=Context(...))` 时传入
3. LangGraph 通过**反射**识别节点函数签名中的 `runtime: Runtime[Context]`，自动注入
4. 节点函数内部通过 `runtime.context.xxx` 访问，**有 IDE 类型提示**

### 23.4 LangChain `create_agent` 中的处理

LangChain 在 `create_agent`（`langchain/agents/factory.py:697-1054`）只是把 `context_schema` 透传给 `StateGraph`：

```python
# langchain/agents/factory.py:1047-1055（节选）
graph: StateGraph[
    AgentState[ResponseT], ContextT, _InputAgentState, _OutputAgentState[ResponseT]
] = StateGraph(
    state_schema=resolved_state_schema,
    input_schema=input_schema,
    output_schema=output_schema,
    context_schema=context_schema,   # ← 原样透传给 LangGraph
)
```

LangChain 自身没有对 `context_schema` 做额外处理，连 docstring 都很简短：

```python
# langchain/agents/factory.py:781
context_schema: An optional schema for runtime context.
```

注意 LangChain `create_agent` 的 middleware 签名里也有 `ContextT`：

```python
middleware: Sequence[AgentMiddleware[StateT_co, ContextT]] = ()
```

这意味着**中间件的方法签名也能拿到 `runtime: Runtime[ContextT]`**，可以在中间件里读 context。本项目暂未用到这个能力，但知道它能用很重要。

### 23.5 为什么我们项目用 ContextVar 而不是 `context_schema`

本项目（`space-aiagent`）注入 `bridge` 和 `current_scene_name` 用的是 **ContextVar**，**不是** `context_schema`。这是个经过权衡的决策：

#### 决策矩阵

| 维度 | `context_schema` | `ContextVar` |
|------|------------------|--------------|
| 注入方式 | `invoke(context=...)` 显式 | `bridge_var.set(...)` 隐式 |
| 类型安全 | ✅ 有 IDE 类型提示 | ❌ 字符串 key |
| 可变 vs 不可变 | **不可变**（运行期不能改） | **可变**（可多次 set/reset） |
| Agent 缓存友好 | ❌ context 是 invoke 参数，缓存有效 | ✅ 与 graph 编译解耦 |
| 跨子 Agent 传播 | ❌ 子 Agent 有独立 invoke | ✅ 协程级传播，子 Agent 直接继承 |
| 学习曲线 | 高（要理解 Runtime 模型） | 低（就是个全局变量） |

#### 关键原因：子 Agent 不共享 invoke

我们项目用 `subagents` 把 Scene Agent / Entity Agent 作为子 Agent 注册。DeepAgents 通过 `task` 工具调用子 Agent 时：

- 子 Agent 是**独立的 CompiledGraph**，有自己的 invoke 链路
- 子 Agent 的 `context` 是它**自己 invoke 时传入**的，**不会从父 Agent 继承**
- 如果用 `context_schema`，必须给每个子 Agent 显式传 context

而 ContextVar 不同：

```python
# 父协程（WebSocket handler）set 一次
bridge_var.set(bridge)

# 父 Agent → 工具 → 子 Agent（task 工具内部 invoke）
# 都在同一个 asyncio task 内，共享同一个 Context
# 子 Agent 的工具函数 bridge_var.get() 照样拿到 bridge
```

ContextVar 的协程级传播特性完美匹配"父 → 子"的调用链，无需任何额外传递。

#### 关键原因：context_schema 会让 `_agent_cache` 失效

本项目用 `_agent_cache` 缓存 Agent 实例（详见 [8.2 节](#82-创建主控-agent)）。如果用 `context_schema`：

```python
# context 是 invoke 参数，每次 invoke 都不同
await agent.ainvoke(
    {"messages": [...]},
    config={"configurable": {"thread_id": "..."}},
    context=AppContext(bridge=bridge, scene_name=...),    # 每次不同
)
```

虽然 graph 本身可以缓存，但每次都要构造新的 Context 实例。ContextVar 则完全在 graph 之外，graph 是真正"无状态"的，缓存最干净。

### 23.6 何时该用 `context_schema`

`context_schema` 适合**真正不可变、与业务运行强绑定**的依赖：

| 场景 | 适合用 `context_schema`？ |
|------|---------------------------|
| 注入数据库连接池 | ✅ 启动时固定，运行期不可变 |
| 注入当前 user_id、tenant_id | ✅ 单次运行内不变 |
| 注入 trace_id 用于日志追踪 | ✅ 一次 invoke 一个 trace |
| 注入业务可变状态（如当前场景名） | ❌ 用户可能中途切换场景 |
| 注入会话级动态资源（如 WebSocket） | ❌ 每个会话独立、可能被替换 |
| 跨子 Agent 共享数据 | ❌ 子 Agent 有独立 invoke |

#### 经典用法示例（理论参考）

```python
from dataclasses import dataclass
from deepagents import create_deep_agent
from langgraph.runtime import Runtime


@dataclass
class AppContext:
    """启动时确定的运行时依赖。"""
    db_conn: DatabaseConnection
    user_id: str
    tenant_id: str


# 在节点函数或 middleware 中通过 runtime 参数注入
async def my_tool(query: str, runtime: Runtime[AppContext]) -> dict:
    db = runtime.context.db_conn        # ← 类型安全，IDE 可跳转
    user_id = runtime.context.user_id
    return await db.fetch(query, user_id)


agent = create_deep_agent(
    model="...",
    tools=[my_tool],
    context_schema=AppContext,          # ← 注册 schema
)

# invoke 时传 context 实例
await agent.ainvoke(
    {"messages": [...]},
    config={"configurable": {"thread_id": "..."}},
    context=AppContext(db_conn=db, user_id="u123", tenant_id="t456"),
)
```

### 23.7 决策复盘：从 `context_schema` 到 `ContextVar` 的完整分析过程

本节是项目实际演进过程的复盘——记录我们是如何**从"看上去很合理"的 `context_schema` 方案，分析到最终选择 `ContextVar + Middleware` 方案**的。这不仅是结论，更重要的是**思考路径**，可以作为类似选型决策的模板。

#### 23.7.1 起点：一个看上去合理的设计提案

最初的提案是这样描述的：

> 因为所有的行为除了创建场景，其余都是基于场景来执行的，所以：
> 1. 在 `models` 中增加一个 `Context` 类，只有一个 `scene_name` 字段
> 2. 前端在 `user_input` 和 `tool_result` 消息中都携带 `sceneName`
> 3. 使用上下文工程：`create_deep_agent(context_schema=Context)`
> 4. 在工具的 `runtime` 参数中取到 `context.scene_name`，没有就返回失败

乍一看很合理：用了 LangGraph 官方的"上下文工程"机制、有类型安全、工具函数能通过 `runtime.context` 拿到值。但当我们逐项验证后，发现这个方案和业务模型有几处严重不匹配。

#### 23.7.2 第一步：问"数据是什么性质"——可变 vs 不可变

任何技术选型的第一步，都是搞清楚**数据的本质属性**。`scene_name` 是什么？

- 不是"启动时确定的依赖"（如数据库连接池）
- 不是"一次运行内固定的元信息"（如 user_id、tenant_id）
- 它是**用户在 Cesium 里当前打开的场景名**——用户随时可以切换场景、关闭场景、新建场景

也就是说：

| 数据性质 | 典型例子 | 是否匹配 `scene_name` |
|---------|---------|----------------------|
| 启动时确定 | DB 连接池、配置 | ❌ 否 |
| 单次 invoke 内固定 | user_id、trace_id | ❌ 否 |
| 会话内可变 | scene_name、当前选中对象 | ✅ 是 |

**关键认知**：`scene_name` 是**业务运行状态**，不是**运行时元信息**。

#### 23.7.3 第二步：核对 `context_schema` 的设计契约

回头看 [23.3 节](#233-langgraph-中的注入链路)的源码——`Runtime` 的注释明确写着：

```python
context: ContextT = field(default=None)
"""Static context for the graph run, like `user_id`, `db_conn`, etc.
Can also be thought of as 'run dependencies'."""
```

**"static"** 和 **"run dependencies"** 这两个词已经说死了：context 是**静态运行依赖**，invoke 时传入后**不能在 graph 内修改**。

如果我们硬要用 `context_schema` 装 `scene_name`：

```python
# 错误用法
@dataclass
class AppContext:
    scene_name: str | None

agent = create_deep_agent(model=..., context_schema=AppContext)

# 第一次用户没场景
await agent.ainvoke(
    {"messages": [HumanMessage(content="创建场景A")]},
    context=AppContext(scene_name=None),
)

# 第二次用户已创建场景，前端再次发消息
await agent.ainvoke(
    {"messages": [HumanMessage(content="加个卫星")]},
    context=AppContext(scene_name="场景A"),   # ← 每次 invoke 都要重新构造
)
```

能跑通，但**每次 invoke 都重新构造 Context**，graph 内部完全感知不到 scene 的变化历史。这违背了 context_schema 的语义。

#### 23.7.4 第三步：考察子 Agent 调用链

这是最致命的一个问题。本项目的架构是：

```
Orchestrator (DeepAgent)
   │ 调用 task 工具
   ▼
Scene Agent / Entity Agent (子 Agent，独立 CompiledGraph)
   │ 调用业务工具
   ▼
add_point_entity / create_scenario / ...
```

**子 Agent 是独立的 CompiledGraph**——通过 `task` 工具被调用，本质上是 `子AgentGraph.ainvoke(...)`。

那么 `context_schema` 在子 Agent 上怎么注入？源码（`deepagents/graph.py:482-493`）明确说：

```python
state_schema ... is also forwarded when compiling declarative SubAgent specs
for the `task` tool, so subagents see the same custom fields as the parent.

CompiledSubAgent runnables do not inherit this schema because they are
already compiled — compile those runnables with a compatible state schema
if they need access to the same custom state fields.
```

注意：这里只提到 `state_schema` 会转发给声明式 SubAgent，**没说 `context_schema` 会自动转发**。即使转发，子 Agent 的 invoke 仍然要**单独传 context 实例**：

```python
# 假想的 task 工具内部实现
async def task(subagent_name, description):
    subagent = self._subagents[subagent_name]
    return await subagent.ainvoke(
        {"messages": [...]},
        context=???,  # ← 这里怎么传？没有显式的传递机制
    )
```

DeepAgents 的 `SubAgentMiddleware` **没有自动把父 Agent 的 context 转发给子 Agent**——因为 context 是 invoke 参数，必须显式传入。

**对比 ContextVar 的传播路径**：

```python
# WebSocket handler（父协程）
async def run_agent(bridge, user_msg):
    bridge_var.set(bridge)
    current_scene_name_var.set(user_msg.current_scene_name)
    try:
        # Orchestrator (DeepAgent) 在同一协程内运行
        await orchestrator.ainvoke({"messages": [...]})

        # Orchestrator 调用 task 工具
        # → SubAgentMiddleware 调用 子 Agent.ainvoke
        # → 子 Agent 的工具函数 add_point_entity 调用
        # 全程在同一 asyncio task 内！
        # 子 Agent 的工具函数 current_scene_name_var.get() 直接拿到值
    finally:
        ...
```

ContextVar 的协程级传播（详见 [2.5 节](#25-contextvar--上下文变量)）天然覆盖了"父 → 子"的调用链。**这是 ContextVar 相对 `context_schema` 的决定性优势**。

#### 23.7.5 第四步：考察 `_agent_cache` 的友好性

本项目用 `_agent_cache` 缓存 Agent 实例（详见 [8.2 节](#82-创建主控-agent)）。`_agent_cache` 缓存的是**编译后的 CompiledGraph**，key 是 `(model_name, subagents_signature)`。

| 方案 | 缓存友好性 |
|------|----------|
| `context_schema` | graph 可缓存，但每次 invoke 要构造 Context 实例（轻量但不为零） |
| `ContextVar` | graph 完全无状态，与缓存解耦 |

ContextVar 方案下，Agent 实例真正"无状态"——同一份 Agent 实例可以服务多个并发会话，每个会话有独立的 ContextVar 值（每个 WebSocket handler 是独立的 asyncio task，独立的 Context）。

#### 23.7.6 第五步：业务规则的强制执行——分散检查 vs 集中校验

提案的第四步是"在工具里检查 scene_name"。这意味着每个工具函数都要重复：

```python
@tool
async def add_point_entity(..., runtime: Runtime[AppContext]):
    if not runtime.context.scene_name:
        return {"success": False, "message": "无场景上下文"}
    # ... 业务逻辑

@tool
async def create_sgp4_orbit(..., runtime: Runtime[AppContext]):
    if not runtime.context.scene_name:
        return {"success": False, "message": "无场景上下文"}
    # ... 业务逻辑

# ... 其他 7 个工具都要重复
```

**重复代码 = bug 温床**。当某个工具的开发者忘了加这个检查，就会让指令漏到前端，前端的 Cesium 才发现"没场景"返回错误——这就是最初想优化的痛点。

我们的方案：把检查集中到中间件：

```python
class ToolValidationMiddleware(AgentMiddleware):
    _SCENE_EXEMPT_TOOLS = frozenset({"create_scenario", "AgentResponse", "task"})

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool_call.get("name", "")

        # 校验 1: bridge 注入（所有远程工具都需要）
        if bridge_var.get() is None:
            return {"success": False, "message": "bridge 未注入"}

        # 校验 2: 场景上下文（白名单外）
        if tool_name not in self._SCENE_EXEMPT_TOOLS and not current_scene_name_var.get():
            return {"success": False, "message": "当前会话没有场景上下文"}

        return await handler(request)   # 通过才放行
```

挂载到 Orchestrator 和**每个子 Agent**（因为子 Agent 有独立 middleware 列表，Orchestrator 的不传递）：

```python
# subagents.py
subagents.append({
    "name": "entity-agent",
    ...
    "middleware": [ToolValidationMiddleware()],   # ← 每个子 Agent 都要挂
})
```

工具函数变薄——只保留业务逻辑：

```python
@tool
async def add_point_entity(name: str, position: dict, ...) -> dict:
    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        tool_func="addPointEntity",
        args={"name": name, "position": position, ...},
    )
```

**收益**：
- 校验逻辑只写一处，未来加新工具不会漏
- 工具函数专注业务，删除了 8 处样板检查
- 中间件是天然的扩展点——参数校验、权限、限流都能在这里加

#### 23.7.7 决策矩阵：三个候选方案的最终对比

| 维度 | 方案 A：`context_schema` | 方案 B：`ContextVar` + 工具内检查 | 方案 C：`ContextVar` + Middleware ✅ |
|------|-------------------------|---------------------------------|-----------------------------------|
| 匹配 scene_name 可变性 | ❌（不可变契约冲突） | ✅ | ✅ |
| 跨子 Agent 传播 | ❌（不自动继承） | ✅ | ✅ |
| 校验集中度 | ❌（分散在每个工具） | ❌（分散在每个工具） | ✅（集中到 middleware） |
| 工具函数清爽度 | ❌（每工具都要 runtime + 检查） | ❌（每工具都要 bridge + scene 检查） | ✅（只业务逻辑） |
| `_agent_cache` 友好 | 🟡（每次 invoke 构造 Context） | ✅ | ✅ |
| IDE 类型提示 | ✅ | ❌（字符串 key） | ❌（字符串 key） |
| 扩展性（未来加校验） | 🟡（要改每个工具） | 🟡（要改每个工具） | ✅（改一处 middleware） |
| 与 LangGraph 官方推荐契合 | ✅（标准 API） | 🟡（Python 标准库，非官方） | 🟡（Python 标准库，非官方） |

**最终决策：方案 C**。

#### 23.7.8 牺牲了什么

诚实地说，方案 C 也付出了代价：

| 代价 | 影响 | 缓解 |
|------|------|------|
| 失去 IDE 类型提示 | 工具函数里 `bridge_var.get()` 没有类型注解 | 用 `ContextVar[WSBridge \| None]` 给泛型，IDE 能识别返回类型 |
| 不是 LangGraph 官方 API | 学习者可能困惑"为什么不用 context_schema" | 本教程 23 章专门解释 |
| ContextVar 跨 Context 有坑 | `asyncio.create_task` 会复制 Context，token 跨 Context 报错 | 详见 [11.6 节](#116-contextvar-跨-context-错误详解)，必须 set/reset 在同一 async def |

但这些代价远小于"匹配业务模型 + 集中校验 + 跨子 Agent 传播"的收益。

#### 23.7.9 决策框架（可推广）

这个分析过程可以提炼成一个**通用的"会话级状态注入"决策框架**：

```
Step 1: 数据性质判断
├── 启动时确定 + 运行期不变 → 用依赖注入（构造函数参数、单例）
├── 单次 invoke 内固定 → 用 context_schema
└── 会话内可变 → 用 ContextVar

Step 2: 调用链传播需求
├── 只在主 Agent 用 → context_schema 或 ContextVar 都行
├── 跨子 Agent 传播 → ContextVar（自动）
└── 跨进程 / 跨服务 → 序列化传递（如 HTTP header、消息体字段）

Step 3: 校验逻辑的位置
├── 每个工具自己检查 → 容易遗漏，初期可接受
├── 集中到 Middleware → 强约束，推荐
└── 提前到 API 网关层 → 适合横切关注点（鉴权、限流）

Step 4: 验证缓存友好性
├── 状态影响 graph 行为 → 不能缓存 graph 实例
├── 状态完全在 graph 外 → graph 可缓存
└── 高并发场景优先选后者
```

把这个框架套用到下次类似决策（如"如何把 user_id 注入到工具"、"如何让审计中间件拿到当前 trace_id"）时，能快速得到合理结论。

#### 23.7.10 小结

- 不要被"上下文工程"这样的术语迷惑——选型的根本是**匹配业务数据性质**
- `context_schema` 适合"启动时确定 + 单次 invoke 内不变"的依赖
- `ContextVar` 适合"会话内可变 + 跨子 Agent 传播"的状态
- 中间件是**集中校验**的最佳位置——比工具内分散检查更安全、更可扩展
- 这个决策框架可推广到其他"会话级状态注入"问题

### 23.8 小结

- **`context_schema` 是 LangGraph 的功能**，DeepAgents 和 LangChain 都是纯透传
- 它适合**运行时不可变的依赖注入**（DB、user_id、trace_id）
- 它**不适合**本项目场景（会话级可变状态、需要跨子 Agent 传播）
- 本项目用 **ContextVar** 是更匹配业务特征的方案
- 理解 `context_schema` 仍有价值——它是 LangGraph 的"官方依赖注入"机制，未来扩展（如多租户、审计中间件）时是首选

---

## 24. `create_deep_agent` 参数全解析（源码级）

### 24.1 函数签名总览

源码位置：`deepagents/graph.py:235-255`

```python
def create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | dict[str, Any] | None = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph: ...
```

**17 个参数**。下面按职责分组讲解，配以源码引用。

### 24.2 模型与工具组（核心输入）

#### `model`：必填，LLM 实例

```python
model: str | BaseChatModel | None = None
```

源码处理（`graph.py:547-567`）：

```python
if model is None:
    warn_deprecated(...)               # 0.5.3+ 警告：必须显式指定
    model = _build_default_model()     # 默认 claude-sonnet-4-6
else:
    model = resolve_model(model)       # 字符串 → 实例（通过 init_chat_model）
```

**三种传法**：

```python
# 1. 字符串（init_chat_model 解析）
create_deep_agent(model="openai:gpt-4o-mini")

# 2. provider:model 字符串
create_deep_agent(model="anthropic:claude-sonnet-4-6")

# 3. 预初始化的 ChatModel 实例（本项目用法）
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="deepseek-chat", base_url="...")
create_deep_agent(model=model)
```

本项目用第 3 种，因为我们要配置 DeepSeek / Qwen 的兼容接口，需要自定义 `base_url`。

#### `tools`：附加工具列表

```python
tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None
```

**关键性质**（源码 docstring 第 301-309 行）：

> These are merged with the built-in tool suite listed above
> (`write_todos`, filesystem tools, `execute`, and `task`).
> Passing tools here is **additive** — it never removes a built-in.
> To drop a built-in tool, register a `HarnessProfile` with `excluded_tools`.

DeepAgents **自带一套内置工具**：

| 内置工具 | 用途 |
|---------|------|
| `write_todos` | 任务清单管理（TodoListMiddleware 提供） |
| `ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` | 文件系统操作（FilesystemMiddleware） |
| `execute` | Shell 命令（需要 SandboxBackend） |
| `task` | 调用子 Agent（SubAgentMiddleware） |

`tools` 参数是**叠加**的。本项目通过 Orchestrator 的 `subagents` 让子 Agent 持有 `entity_management`、`orbit_management` 等业务工具组。

源码处理（`graph.py:582-588`）：

```python
_tools = _apply_tool_description_overrides(
    tools,
    _profile.tool_description_overrides,   # 按 HarnessProfile 调整描述
)
```

### 24.3 提示词与中间件

#### `system_prompt`：自定义系统提示词

```python
system_prompt: str | SystemMessage | None = None
```

源码 docstring（第 310-326 行）强调**拼接顺序**：

> Whatever you pass here always sits **before** the SDK's default
> deep-agent prompt and any model-tuning suffix from a registered
> `HarnessProfile`. Sections are joined by a blank line.

最终 system prompt 的组装顺序：

```
[1] 用户的 system_prompt（如本项目 prompts/orchestrator.md）
[2] SDK 默认的 deep-agent 提示词
[3] HarnessProfile 的 model-tuning 后缀
```

如果传 `SystemMessage` 而不是 `str`，会保留 `cache_control` 标记——这是给 Anthropic Prompt Cache 用的。

本项目用法（`agents/orchestrator.py`）：

```python
system_prompt = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")
agent = create_deep_agent(
    model=model,
    system_prompt=system_prompt,
    ...
)
```

#### `middleware`：用户中间件

```python
middleware: Sequence[AgentMiddleware] = ()
```

**完整组装顺序**（源码 docstring 第 327-365 行）：

```
Base stack:
  ├─ TodoListMiddleware
  ├─ SkillsMiddleware                (if skills provided)
  ├─ FilesystemMiddleware
  ├─ SubAgentMiddleware              (if inline subagents)
  ├─ SummarizationMiddleware
  ├─ PatchToolCallsMiddleware
  └─ AsyncSubAgentMiddleware         (if async subagents)

*User middleware inserted here*       ← 你传的 middleware 参数

Tail stack:
  ├─ profile.extra_middleware        (if any)
  ├─ _ToolExclusionMiddleware        (if profile.excluded_tools)
  ├─ AnthropicPromptCachingMiddleware (unconditional)
  ├─ MemoryMiddleware                (if memory provided)
  └─ HumanInTheLoopMiddleware        (if interrupt_on provided)
```

本项目用法（`agents/orchestrator.py`）：

```python
middleware=[
    ToolValidationMiddleware(),                      # bridge + scene 校验
    LoggingMiddleware(thread_id=thread_id),          # 可观测性
],
```

详见 [17 章](#17-agentmiddleware-中间件深度讲解)。

### 24.4 子 Agent 调度

#### `subagents`：声明式子 Agent 列表

```python
subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None
```

**三种形态**（源码 docstring 第 370-401 行）：

| 类型 | 字段 | 调用方式 |
|------|------|---------|
| `SubAgent`（声明式同步） | `name`/`description`/`system_prompt`/`tools` | `task` 工具调用 |
| `CompiledSubAgent`（已编译） | `name`/`description`/`runnable` | `task` 工具调用 |
| `AsyncSubAgent`（远程异步） | `name`/`description`/`graph_id`/`url`/`headers` | 异步任务工具 |

**默认 general-purpose 子 Agent**：

```python
# graph.py:396-401
If no subagent named `general-purpose` is provided, a default
general-purpose synchronous subagent is added automatically unless
the active harness profile disables it. With no synchronous
subagents in play — none passed and the default disabled via
`general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`
— the `task` tool is not exposed.
```

不传 `subagents` 也有一个默认的通用子 Agent。本项目传了 `scene-agent` / `entity-agent`，覆盖了一部分能力。

源码处理（`graph.py:598-613`）：

```python
for spec in subagents or []:
    if "graph_id" in spec:
        async_subagents.append(cast("AsyncSubAgent", spec))
        continue
    if "runnable" in spec:
        inline_subagents.append(spec)
    else:
        raw_subagent_model = spec.get("model", model)
        subagent_model = resolve_model(raw_subagent_model)
        ...
```

每个 `SubAgent` 自带 middleware 字段（源码 `subagents.py:95`）：

```python
class SubAgent(TypedDict):
    name: str
    description: str
    system_prompt: str
    tools: NotRequired[Sequence[BaseTool | Callable]]
    model: NotRequired[...]
    middleware: NotRequired[list[AgentMiddleware]]   # ← 子 Agent 独立中间件
    ...
```

**关键认知**：子 Agent 的 middleware **不会**从 Orchestrator 继承，必须独立挂载。本项目在 `subagents.py` 给每个子 Agent 显式注入 `ToolValidationMiddleware()`。

### 24.5 文件系统 / 知识 / 权限

#### `skills`：Skill 路径列表

```python
skills: list[str] | None = None
```

源码 docstring 第 403-410 行：

> List of skill source paths (e.g., `["/skills/user/", "/skills/project/"]`).
> Paths must be specified using POSIX conventions (forward slashes)
> and are relative to the backend's `root`.

Skills 是 DeepAgents 的"动态能力扩展"机制，类似 Claude 的 Skills。本项目未使用（业务工具组通过子 Agent 注册，不走 Skills）。

#### `memory`：知识文件列表

```python
memory: list[str] | None = None
```

源码 docstring 第 411-416 行：

> List of memory file paths (`AGENTS.md` files) to load
> (e.g., `["/memory/AGENTS.md"]`).
>
> Memory is loaded at agent startup and added into the system prompt.

本项目用法：

```python
agent = create_deep_agent(
    ...
    memory=["AGENTS.md"],   # 加载 knowledge/AGENTS.md
)
```

实现机制：通过 `MemoryMiddleware` 在 agent 启动时读取文件，把内容拼到 system prompt。

#### `permissions`：文件系统权限规则

```python
permissions: list[FilesystemPermission] | None = None
```

源码 docstring 第 417-441 行：

> Rules are evaluated in declaration order; the first match wins.
> If no rule matches, the call is allowed.
>
> Each rule's `mode` can be:
> - `"allow"` (default): the call proceeds.
> - `"deny"`: the tool returns a permission-denied error.
> - `"interrupt"`: the call pauses for human approval.

支持三种模式：允许、拒绝、人工审批。本项目暂未使用（所有文件操作都走虚拟后端，无敏感数据）。

#### `backend`：文件后端

```python
backend: BackendProtocol | BackendFactory | None = None
```

源码处理（`graph.py:590`）：

```python
backend = backend if backend is not None else StateBackend()
```

默认是 `StateBackend`（虚拟文件系统，存在 graph state 里）。本项目用 `FilesystemBackend`：

```python
backend = FilesystemBackend(
    root_dir=str(_KNOWLEDGE_DIR),
    virtual_mode=True,
)
```

### 24.6 控制流参数

#### `interrupt_on`：人工审批

```python
interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
```

源码 docstring 第 448-469 行：

> Mapping of tool names to interrupt configs.
> Pass to pause agent execution at specified tool calls for human
> approval or modification.

**子 Agent 继承规则**：

| 子 Agent 类型 | 是否继承父级 `interrupt_on` |
|--------------|---------------------------|
| `SubAgent`（声明式） | ✅ 默认继承；自己有 `interrupt_on` 则覆盖 |
| `CompiledSubAgent` | ❌ 不继承，需要自己编译时配置 |
| `AsyncSubAgent` | ❌ 不继承，远程配置 |

本项目未使用——所有工具调用都是自动执行。如果未来要加"删除场景前需要用户确认"，可以这样写：

```python
create_deep_agent(
    ...
    interrupt_on={"delete_scene": True},
)
```

#### `response_format`：结构化输出

```python
response_format: ResponseFormat[ResponseT] | type[ResponseT] | dict[str, Any] | None = None
```

详见 [18 章](#18-agent-结构化输出response_format-详解)。本项目用 `ToolStrategy(AgentResponse)` 强制 LLM 输出 JSON。

### 24.7 Schema 参数（核心扩展点）

#### `state_schema`：可变状态扩展

```python
state_schema: type[DeepAgentState] | None = None
```

源码 docstring 第 471-505 行：

> Custom state schema for the agent graph. Must be a `TypedDict`
> subclass of `DeepAgentState` so the built-in `DeltaChannel` reducer
> on `messages` is preserved.

用法：

```python
from deepagents.graph import DeepAgentState


class MyState(DeepAgentState):
    page_url: str
    file_urls: list[str]


agent = create_deep_agent(model=..., state_schema=MyState)
```

源码处理（`langchain/agents/factory.py:1037-1055`）：

```python
base_state = state_schema if state_schema is not None else AgentState
# middleware schemas first, base_state last so it wins any field conflict
state_schemas: list[type] = [*(m.state_schema for m in middleware), base_state]
resolved_state_schema, input_schema, output_schema = _resolve_schemas(state_schemas)

graph = StateGraph(
    state_schema=resolved_state_schema,
    input_schema=input_schema,
    output_schema=output_schema,
    context_schema=context_schema,
)
```

**重要提示**：官方推荐**优先用 middleware 扩展 state**（而不是直接传 `state_schema`），让字段作用域更明确。本项目目前未扩展 state。

#### `context_schema`：不可变运行时上下文

详见 [23 章](#23-deepagents-context_schema-源码解析)。本项目**未使用**，改用 ContextVar。

### 24.8 持久化与基础设施

#### `checkpointer`：会话持久化

```python
checkpointer: Checkpointer | None = None
```

**Passed through to `create_agent`**。本项目用 SQLite：

```python
from langgraph.checkpoint.aiosqlite import AsyncSqliteSaver

checkpointer = AsyncSqliteSaver.from_conn_string("checkpoints.db")
agent = create_deep_agent(
    ...
    checkpointer=checkpointer,
)
```

checkpointer 是 LangGraph 的核心——它让 graph 的 state 在多次 invoke 之间持久化。这就是"多轮对话"能记住上下文的根本原因。

#### `store`：跨会话存储

```python
store: BaseStore | None = None
```

**Passed through to `create_agent`**。

- `checkpointer` 是**单 thread 内**的持久化（按 `thread_id` 隔离）
- `store` 是**跨 thread**的持久化（用户级 / 全局级）

本项目未使用 store。

#### `debug`：调试模式

```python
debug: bool = False
```

开启后 LangGraph 会打印详细的执行日志（每个节点的 input/output）。生产环境不要开。

#### `name`：Agent 名称

```python
name: str | None = None
```

用于 trace（LangSmith 等可观测性平台）。建议设置为业务含义的名字，如 `"orchestrator"`。

#### `cache`：缓存

```python
cache: BaseCache | None = None
```

LangGraph 的 LLM 调用缓存。同样的输入两次调用，第二次直接从缓存返回。本项目未启用（DeepSeek/Qwen 的结果不太适合缓存）。

### 24.9 参数使用速查

| 参数 | 必填 | 默认 | 本项目用法 |
|------|------|------|-----------|
| `model` | ✅ | `claude-sonnet-4-6`（已废弃默认） | 显式传 `ChatOpenAI`（DeepSeek/Qwen） |
| `tools` | ❌ | `()` | Orchestrator 不传，子 Agent 各自挂 |
| `system_prompt` | ❌ | SDK 默认 | `prompts/orchestrator.md` |
| `middleware` | ❌ | `()` | `[ToolValidationMiddleware, LoggingMiddleware]` |
| `subagents` | ❌ | `None`（自动加 general-purpose） | `[scene-agent, entity-agent]` |
| `skills` | ❌ | `None` | 未用 |
| `memory` | ❌ | `None` | `["AGENTS.md"]` |
| `permissions` | ❌ | `None` | 未用 |
| `backend` | ❌ | `StateBackend()` | `FilesystemBackend` |
| `interrupt_on` | ❌ | `None` | 未用 |
| `response_format` | ❌ | `None` | `ToolStrategy(AgentResponse)` |
| `state_schema` | ❌ | `AgentState` | 未扩展 |
| `context_schema` | ❌ | `None` | **未用，用 ContextVar 替代** |
| `checkpointer` | ❌ | `None` | `AsyncSqliteSaver`（SQLite） |
| `store` | ❌ | `None` | 未用 |
| `debug` | ❌ | `False` | dev/staging 不开 |
| `name` | ❌ | `None` | 未设置 |
| `cache` | ❌ | `None` | 未用 |

### 24.10 本项目实际调用代码

把所有参数串起来，这是 `space-aiagent` 项目 Orchestrator 的真实调用：

```python
# agents/orchestrator.py（简化版）
from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import ToolStrategy

from space_aiagent.middleware import LoggingMiddleware, ToolValidationMiddleware
from space_aiagent.models.response_schema import AgentResponse


_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def create_orchestrator(
    subagents: list[dict],
    checkpointer,
    thread_id: str,
):
    """创建主控 Agent。"""
    system_prompt = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")
    model = build_model()

    backend = FilesystemBackend(
        root_dir=str(_KNOWLEDGE_DIR),
        virtual_mode=True,
    )

    agent = create_deep_agent(
        model=model,                                    # 1. LLM 实例
        system_prompt=system_prompt,                    # 2. 系统提示词
        subagents=subagents,                            # 3. 子 Agent 列表
        middleware=[                                    # 4. 中间件
            ToolValidationMiddleware(),                 #    bridge + scene 校验
            LoggingMiddleware(thread_id=thread_id),     #    可观测性
        ],
        backend=backend,                                # 5. 文件后端（虚拟）
        memory=["AGENTS.md"],                           # 6. 知识文件
        response_format=ToolStrategy(AgentResponse),    # 7. 结构化输出
        checkpointer=checkpointer,                      # 8. 会话持久化
    )
    return agent
```

**用到的参数**：8 个（model / system_prompt / subagents / middleware / backend / memory / response_format / checkpointer）。
**未用到的参数**：9 个（tools / skills / permissions / interrupt_on / state_schema / context_schema / store / debug / name / cache）。

### 24.11 小结

- `create_deep_agent` 是 DeepAgents 的入口，本质是**预配置版的 `create_agent`**
- 它**预装了一套内置工具和中间件**（todo、filesystem、subagent 等）
- 17 个参数中本项目用到 8 个，其余按业务需要再启用
- `context_schema` 是 LangGraph 的不可变上下文机制，本项目用 ContextVar 替代
- 理解每个参数的源码处理逻辑，有助于后续扩展（如多租户、审计、缓存优化）

---

## 25. LangGraph Command —— 状态更新 + 控制流导航

> LangGraph 的 `Command` 对象是一个强大的工具，用于在节点中同时实现**状态更新**和**控制流导航**。它允许开发者在动态场景中灵活地管理流程和状态。

### 25.1 Command 是什么

`Command` 是 LangGraph 提供的控制流原语。节点（包括普通节点、工具节点、中间件）除了可以"返回 state 字典"之外，还可以"返回 `Command`"——后者把两件事打包成原子操作：

1. **状态更新** (`update` 字段)：要写入 state 的新数据（任意字段，不限于 messages）
2. **控制流** (`goto` 字段)：下一步去哪个节点（节点名 / `END` / `Send` 列表）

"原子"的关键含义是：状态更新和路由一起执行，不会出现"状态写了但路由错了"或"路由变了但状态没更新"的不一致。

#### 基本语法

```python
from langgraph.graph import END
from langgraph.types import Command

# 工具或节点函数的返回值
return Command(
    update={"key": "value"},   # 写入 state（任意字段，多个也可以）
    goto="next_node",          # 路由到下一个节点
)
```

#### 和"返回 state dict"的对比

```python
# 方式 1：返回 state 字典（普通节点标准写法）
def my_node(state):
    return {"counter": state["counter"] + 1}    # 写入 state，下一站由图的边决定

# 方式 2：返回 Command（动态路由场景）
def my_node(state):
    return Command(
        update={"counter": state["counter"] + 1},
        goto="agent" if state["counter"] < 3 else END,
    )
```

方式 1 适合**固定流程**（图的边预先连好）；方式 2 适合**动态决策**（运行时才知道下一步去哪）。

#### 返回类型注解

LangGraph 1.x 的 `awrap_tool_call` 等中间件方法签名已经显式支持 `Command`：

```python
# langchain/agents/middleware/types.py（langgraph 1.2+）
async def awrap_tool_call(
    self,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    ...
```

也就是说，从中间件返回 `Command` 是**官方支持**的用法，不是 hack。

### 25.2 项目中的实际用法

本项目的 `ToolValidationMiddleware` 是 `Command` 的典型应用场景。当工具调用前置校验失败（如"无场景上下文"）时，中间件需要做两件事：

1. 把 `NO_SCENE` 错误写入 state（让 orchestrator LLM 后续能看到）
2. **立即终止子 Agent 图**（不要再让子 Agent LLM 浪费一次调用去"解释"这个错误）

这正是 `Command` 的拿手好戏：

```python
# src/space_aiagent/middleware/tool_validation.py
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

class ToolValidationMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id", "")

        # ... bridge 校验略 ...

        if tool_name not in self._SCENE_EXEMPT_TOOLS and not current_scene_name_var.get():
            logger.warning("%s 校验失败: 无场景上下文", tool_name)
            shortcut = _SHORTCUT_RESPONSES["no_scene"]
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps({
                                "success": False,
                                "code": shortcut.code,
                                "status": shortcut.status,
                                "message": shortcut.summary,
                            }, ensure_ascii=False),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
                goto=END,   # 子 Agent 图直接终止，不再调子 Agent LLM
            )

        return await handler(request)
```

#### 对比"纯 ToolMessage"vs"Command(goto=END)"

如果只 `return ToolMessage(...)` 不带 Command：

```
子 Agent LLM #1: "调 delete_scene"
    ↓
Middleware: 无场景 → return ToolMessage(JSON 错误)
    ↓
子 Agent LLM #2: 看到 ToolMessage，"哦没场景，停止"   ← 多这一次
    ↓
子 Agent 结束，结果回到 orchestrator 的 task 工具
```

加上 `Command(goto=END)` 后：

```
子 Agent LLM #1: "调 delete_scene"
    ↓
Middleware: 无场景 → return Command(update=ToolMessage, goto=END)
    ↓
╳ 子 Agent 图直接终止（无 LLM #2）
    ↓
ToolMessage 作为子 Agent 最终结果回到 orchestrator 的 task 工具
```

省的就是 LLM #2 那次调用——它看到"无场景上下文"也没什么有用的事可做。

### 25.3 动态控制流

`goto` 字段支持三种形态，覆盖从简单到复杂的路由需求。

#### 形态 1：字符串（路由到指定节点）

```python
return Command(
    update={"status": "need_user_input"},
    goto="human_input_node",
)
```

适合"我知道接下来要去哪"的固定跳转。

#### 形态 2：`END`（终止当前图）

```python
from langgraph.graph import END

return Command(
    update={"final_answer": "..."},
    goto=END,
)
```

适合"工作流完成"或"早退"场景。本项目中间件就是这种用法——校验失败立即终止子 Agent 图。

#### 形态 3：`Send` 列表（并行分发）

```python
from langgraph.types import Send

return Command(
    update={},
    goto=[
        Send("worker_node", {"task": "查卫星"}),
        Send("worker_node", {"task": "查地面站"}),
        Send("worker_node", {"task": "查传感器"}),
    ],
)
```

一次返回把任务分发给多个节点并行执行。Map-Reduce 模式常用。

#### 条件路由示例

```python
from langgraph.graph import END
from langgraph.types import Command
from typing import Literal

def router_node(state) -> Command[Literal["agent", "synthesizer", "__end__"]]:
    """根据 state 决定下一步去哪"""
    if state.get("error"):
        return Command(update={"error_handled": True}, goto=END)

    if len(state["research_results"]) >= 3:
        return Command(goto="synthesizer")

    return Command(goto="agent")
```

这是 `Command` 最强大的用法：**把路由决策从"图的边"挪到"节点内部"**。图的边是静态的（编译时确定），节点内的 Command 路由是动态的（运行时决定）。

### 25.4 子图导航 `graph=Command.PARENT`

DeepAgents 是 orchestrator + subagents 架构（参见 24.4 子 Agent 调度）。子 Agent 在 LangGraph 里是一个**子图**（subgraph），有自己的节点和状态，嵌套在 orchestrator 这张父图里。

#### 父子图关系示意

```
orchestrator 父图
├── agent 节点（orchestrator LLM）
├── tools 节点
│   └── task 工具 → 触发子 Agent 子图执行
│       │
│       ▼
│       scene-agent 子图（独立的节点和状态）
│       ├── agent 节点（子 Agent LLM）
│       └── tools 节点
│           └── delete_scene 工具
│               ↑ Middleware 在这里跑
└── END
```

#### 默认 `goto=END` 只终止当前图

子图内部的 `Command(goto=END)` 结束的是**子图本身**：

```python
# 在子 Agent 的中间件里
return Command(update={...}, goto=END)
# ↑ 子 Agent 子图终止
# → 控制权回到父图的 task 工具，父图继续执行（orchestrator LLM 仍会被调）
```

这正是本项目当前用法（C1 方案）：省的是子 Agent LLM 调用，orchestrator LLM 仍会跑。

#### `graph=Command.PARENT` 跨层级终止

如果想让子 Agent 的中间件**直接终止整个 orchestrator**（连 orchestrator LLM 也跳过），用 `Command.PARENT`：

```python
from langgraph.types import Command

# 在子 Agent 的中间件里，跨层级终止父图
return Command(
    graph=Command.PARENT,            # 目标图改为父图
    update={"messages": [AIMessage(content="...")]},
    goto=END,                         # 父图直接 END
)
```

效果：

```
orchestrator 父图
├── agent 节点（orchestrator LLM）✅ 已跑
├── tools 节点
│   └── task 工具 → 子 Agent 子图
│       └── middleware 返回 Command(PARENT, goto=END) ❗
│           ↓
│           ╳ 直接终止父图（orchestrator 后续节点全跳过）
└── END
```

这就是 C2 方案。代价是：orchestrator LLM 不再产出 AgentResponse，websocket handler 必须从 state（最后的 AIMessage）里读取最终回复——渲染路径要重写。

本项目当前**没有用** `Command.PARENT`，因为：
- 当前业务规模下省 1 次 orchestrator LLM 调用不划算
- websocket 改造成本（要从 event 驱动改成 state 驱动）

未来如果对延迟/成本敏感，可考虑切到 C2。

### 25.5 注意事项

#### 返回类型注解

节点函数返回 `Command` 时，**强烈建议**在类型注解里列出所有可能的目标节点：

```python
from typing import Literal
from langgraph.types import Command

def router_node(state) -> Command[Literal["agent", "synthesizer", "__end__"]]:
    """泛型参数告诉 LangGraph 可能的路由目标"""
    if state.get("error"):
        return Command(goto=END)          # __end__
    if len(state["results"]) >= 3:
        return Command(goto="synthesizer")
    return Command(goto="agent")
```

**为什么重要**：

1. **图形可视化**：LangGraph Studio / Mermaid 渲染状态图时，会根据这些注解画"潜在边"。没有注解的话，图会漏掉节点间的动态跳转
2. **静态检查**：类型检查器能发现"你说要去 X 节点，但 X 不存在"这类错误
3. **可读性**：节点函数签名一眼看出"它可能去哪"，不用读完整个函数体

如果不写泛型，Command 仍然能工作，但**会失去这些工程性收益**。生产代码建议都写。

#### 状态冲突与 Reducer

当子图通过 `Command(graph=Command.PARENT, update={...})` 向父图更新状态时，如果父子图都定义了**同名字段**（典型场景：都有 `messages`），LangGraph 默认行为是**子图覆盖父图**——这通常不是你想要的。

**问题示例**：

```python
# 假设父子图 state 都有 messages 字段，但没定义 Reducer

# 父图初始: messages = [HumanMessage("你好")]
# 子图执行后: messages = [AIMessage("子 Agent 的回复")]
# Command(PARENT, update={messages: [...]}) 触发时：
#   → 父图 messages 被替换成子图的 [AIMessage("子 Agent 的回复")]
#   → HumanMessage("你好") 没了！
```

**解决方案：定义 Reducer**

Reducer 是 LangGraph 里"字段如何合并"的规则。对 messages 字段，标准做法是用 `add_messages` 让追加而非覆盖：

```python
from typing import Annotated, TypedDict
from langgraph.graph import add_messages

class ParentState(TypedDict):
    messages: Annotated[list, add_messages]    # Reducer：追加合并
    # 其他字段...
```

有了 Reducer 后，子图的 messages 会被**追加**到父图的 messages 后面，而不是替换：

```python
# 父图初始: messages = [HumanMessage("你好")]
# 子图 update: messages = [AIMessage("子 Agent 的回复")]
# 合并后: messages = [HumanMessage("你好"), AIMessage("子 Agent 的回复")]   ✅
```

本项目的 `create_orchestrator` 通过 `create_deep_agent` 隐式使用了 LangGraph 的默认 messages Reducer，所以 messages 字段自动是追加语义——这也是为什么 25.2 节中间件返回的 ToolMessage 能正确并入会话历史。

> **参见**：8.2 节"创建主控 Agent"和 24.7 节"Schema 参数"对 state_schema / Reducer 的讨论。

#### `update` 必须是 state schema 里定义的字段

```python
# ❌ 错误：state 里没定义 "foo" 字段
return Command(update={"foo": "bar"}, goto=END)

# ✅ 正确：state schema 里有 messages 字段
return Command(update={"messages": [...]}, goto=END)
```

如果需要写入新字段，先在 state_schema 里声明。

### 25.6 应用场景

#### 场景 1：多智能体交接

orchestrator 把任务交给子 Agent 后，子 Agent 可以用 `Command` 把结果直接交给下一个智能体，无需 orchestrator 中转：

```python
# research_agent 完成后，直接交给 writer_agent
def research_agent(state) -> Command[Literal["writer_agent"]]:
    findings = do_research(state["topic"])
    return Command(
        update={
            "research_findings": findings,
            "current_stage": "writing",
        },
        goto="writer_agent",
    )
```

这种"agent 间直接通信"的模式比"全部经过 orchestrator 中转"更高效——省一次 LLM 调用。

#### 场景 2：动态流程控制

根据工具返回值决定下一步，工具/中间件不再只能"返回值等 LLM 决策"：

```python
async def search_tool(state):
    """根据搜索结果决定走澄清还是直接回答"""
    result = await api.search(state["query"])

    if result["needs_clarification"]:
        # 搜索结果模糊 → 跳到澄清节点
        return Command(
            update={"clarification_options": result["options"]},
            goto="clarify_node",
        )

    if result["confidence"] > 0.9:
        # 高置信度 → 直接给答案
        return Command(
            update={"final_answer": result["answer"]},
            goto=END,
        )

    # 中等置信度 → 让 LLM 综合
    return Command(
        update={"search_results": result},
        goto="synthesizer_node",
    )
```

工具内部就能做流程决策，不用把这些逻辑塞到 LLM prompt 里靠 LLM 自己推理。

#### 场景 3：确定性短路（本项目）

已知道具调用必然失败的 case，跳过 LLM 推理直接终止：

```python
# 本项目 tool_validation.py
if not current_scene_name_var.get():
    # 已知必然失败，不需要 LLM 推理
    return Command(
        update={"messages": [ToolMessage(content="无场景上下文...", tool_call_id=...)]},
        goto=END,
    )
```

LLM 退化为"只处理开放性 case"的角色，确定性 case 由规则短路。这是省 token、降延迟的关键模式。

#### 场景 4：Map-Reduce 并行

把一个大任务拆成多个子任务并行执行：

```python
def fan_out_node(state) -> Command:
    """把一个大查询拆成 N 个小查询并行"""
    return Command(
        update={},
        goto=[
            Send("search_worker", {"query": q}) 
            for q in state["sub_queries"]
        ],
    )
```

所有 `search_worker` 实例并行执行，结果由 Reducer 汇总到 state。

### 25.7 为什么不直接 `return Command(update=AIMessage, goto=END)`

这是本项目踩坑后专门讨论过的问题，详细讲一下。

#### 直觉陷阱

看到 `Command(goto=END)` 能跳过子 Agent LLM 调用，自然会想：

> 既然子 Agent LLM 的回复内容我已经知道了（NO_SCENE 模板），为什么不直接塞一条 `AIMessage` 当成子 Agent 的回复？还要绕一圈放 `ToolMessage` 干嘛？

```python
# 直觉写法（用户提议）
return Command(
    update={"messages": [AIMessage(content="还没有打开的场景...")]},
    goto=END,
)
```

看起来更"直接"——少一层 ToolMessage 中转。**但这会被 LLM API 拒绝**。

#### LLM API 的硬约束

OpenAI / DeepSeek / Qwen 等 OpenAI 兼容 API 都有这条规则：

> **assistant 消息带 `tool_calls` 时，后面必须跟 `tool` 角色消息**响应每个 `tool_call_id`

否则返回 400 错误：

```
An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id'. The following tool_call_ids did not
have response messages: call_abc
```

这是**服务端硬校验**，不是 LangGraph 的限制。

#### 状态序列对比

考虑子 Agent 调 `delete_scene`（tool_call_id=`call_abc`）的两种写法：

**正常序列（用 ToolMessage，合法）**：

```
[
  HumanMessage("删除场景"),
  AIMessage(tool_calls=[{name: "delete_scene", id: "call_abc"}]),  # AI 决定调工具
  ToolMessage(content="无场景上下文...", tool_call_id="call_abc"),  # 关闭 tool_call
  AIMessage(content="还没有打开的场景..."),                          # 子 Agent LLM 的回复
]
```

**跳过 ToolMessage（用户提议，非法）**：

```
[
  HumanMessage("删除场景"),
  AIMessage(tool_calls=[{name: "delete_scene", id: "call_abc"}]),  # AI 决定调工具
  AIMessage(content="还没有打开的场景..."),                          # ❌ 跳过了 ToolMessage
]
```

第二条相当于对 API 说"我决定调工具，但不告诉你结果，自己又说了句话"——API 不接受。

#### 为什么"看起来可能 work"

如果用 `Command(goto=END)` 终止子 Agent 图，**子 Agent 的 LLM 不会再被调用**，所以这条非法序列**永远不会发给 API**——它只是躺在 checkpointer 里。然后 DeepAgents 的 `task` 工具会把子 Agent 的结果**重新包装**成一个 ToolMessage 喂给 orchestrator：

```
orchestrator 看到的（合法）:
[
  HumanMessage("删除场景"),
  AIMessage(tool_calls=[{name: "task", id: "call_xyz"}]),          # orchestrator AI 调 task
  ToolMessage(content="<DeepAgents 包装的子 Agent 结果>", tool_call_id="call_xyz"),
]
```

所以 orchestrator 那边序列合法，侥幸能跑。

**但**——依赖两个不确定 assumption：

1. **DeepAgents 怎么提取子 Agent 的"结果"**：DeepAgents 内部 (`subagents.py` 的 `_return_command_with_state_update`) 通常从子 Agent 的**最后一条 AIMessage** 取内容。如果子 Agent state 里最后一条是 AIMessage，DeepAgents 大概率能正确提取——但这不是它的"正常"输入，行为靠运气
2. **未来版本会不会加校验**：现在 LangGraph 不校验 state 里的消息序列，未来版本未必。一旦加了校验，这条路径会突然坏掉

#### 何时 AIMessage 是合法的

`Command(graph=Command.PARENT, update={"messages": [AIMessage(...)]}, goto=END)` 是合法的——因为 AIMessage 不跟在子 Agent 的 tool_call 后面，而是直接成为 orchestrator state 的一部分。

这就是 C2 方案（25.4 节）的核心思路。AIMessage 用在 PARENT Command 里合法；用在子 Agent 自己的 Command 里不合法。

#### 结论

| 写法 | state 序列 | API 合法性 |
|------|-----------|----------|
| `Command(update=ToolMessage, goto=END)` | `[AI tool_call, ToolMessage]` | ✅ 合法 |
| `Command(update=AIMessage, goto=END)` （子 Agent 级） | `[AI tool_call, AIMessage]` | ❌ 子图内部非法（侥幸 work） |
| `Command(graph=PARENT, update=AIMessage, goto=END)` | AIMessage 直接进父图 state | ✅ 合法 |

`Command(goto=END)` 节省的是"解释工具结果"那次 LLM 调用，**但不能跳过 LLM API 协议层的状态闭合动作**——ToolMessage 是必须的。

### 25.8 Command vs 普通返回值 vs 异常

最后用一张对比表收尾，把这几种"节点返回方式"放在一张图里：

| 方式 | 状态更新 | 控制流 | 适用场景 | 风险 |
|------|---------|-------|---------|------|
| `return state_dict` | ✅ | ❌ 走默认边 | 普通节点，下一站由图的边预先连好 | 无 |
| `return ToolMessage(...)` | ✅（单一 messages） | ❌ 默认回 agent 节点 | 工具节点标准返回 | 无 |
| `return Command(update, goto)` | ✅（任意字段） | ✅ goto 任意节点 | 动态路由、子图导航、早退 | 低（框架原生支持） |
| `raise Exception(...)` | ❌ **不持久化** | ❌ 终止 | 错误处理（慎用） | 高（破坏状态一致性） |

#### 异常路径的隐藏代价

本项目踩过的坑：曾经用 `raise ShortcutResponse(...)` 实现短路，导致 LangGraph 把工具调用标成 **"cancelled"**，state 没正确持久化。下一轮 LLM 加载历史时看到的是 `[AI tool_call, Tool(cancelled), Human("好的")]`，完全没有"还没打开场景"的痕迹，多轮对话完全断裂。

```
异常路径:
Middleware raise ShortcutResponse
    ↓
LangGraph: "工具被异常打断，标成 cancelled"
    ↓
websocket 捕获异常，渲染响应发给用户 ✅
    ↓
但 checkpointer 里: [AI tool_call, Tool(cancelled)]   ← 缺真正响应
    ↓
下一轮用户: "好的"
    ↓
LLM: 完全失忆，重复同样的错误响应
```

`Command(goto=END)` 走的是图的正常节点返回路径，state 由框架按 `update` 字段自动持久化。这正是为什么本项目的 NO_SCENE 短路从异常改成 Command——**修了多轮对话断裂的 bug**。

#### 选择指南

| 你的需求 | 用什么 |
|---------|-------|
| 普通节点，按预定义流程走 | `return state_dict` |
| 工具正常返回结果 | `return ToolMessage(...)` |
| 工具失败但要让 LLM 兜底 | `return ToolMessage(content=error)` |
| 已知结果，跳过 LLM 早退 | `return Command(update=ToolMessage, goto=END)` |
| 跨层级终止整个工作流 | `return Command(graph=PARENT, update=..., goto=END)` |
| 不可恢复的系统故障 | `raise Exception(...)` （配合外层 try/except） |

**铁律**：异常只用于"真正的错误"，**不要用异常做控制流**。LangGraph 提供了 `Command` 这个原生原语来处理"非默认路由"，滥用异常会破坏状态一致性。

### 25.9 小结

- `Command` 是 LangGraph 的"状态更新 + 路由"二合一原语，把这两件事打包成原子操作
- `goto` 支持三种形态：节点名（静态跳转）、`END`（终止当前图）、`Send` 列表（并行分发）
- 子图内 `goto=END` 只终止子图；`graph=Command.PARENT` 跨层级终止父图
- 节点函数返回 `Command` 时建议加泛型类型注解（`Command[Literal[...]]`），便于可视化和静态检查
- 子图向父图更新同名字段时需要 Reducer（如 `add_messages`）避免覆盖
- **`ToolMessage` 是 LLM API 协议层的"关闭 tool_call"动作，不能省**——`Command(goto=END)` 只能跳过 LLM 调用，不能跳过协议层的状态闭合
- 异常做控制流是反模式：绕过 checkpointer 持久化机制，破坏多轮对话状态一致性
