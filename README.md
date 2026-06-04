# space-aiagent

航天分析平台智能助手 - 基于 DeepAgent (LangChain) 的多 Agent 系统

## 项目背景

本项目为上海航天研究院 805 所定制的航天分析平台（基于 Cesium 的航天 GIS 系统）提供 AI 智能助手能力。

### 业务场景

航天分析平台是一个基于 Cesium（前端 JS 库）的 GIS 系统，核心能力均以前端技术实现。智能助手的交互入口在前端，用户通过自然语言提出需求（如"创建场景"、"添加卫星"），后端 Agent 服务解析意图后，生成操作指令通过 WebSocket 发送到前端，前端根据指令调用 Cesium API 完成操作。

### 通信架构

```
用户输入 → 智能助手交互端（前端） → WebSocket → AI Agent 服务（后端）
                                                    ↓
前端调用 Cesium API ← 操作指令 ← WebSocket ← Agent 工具调用
```

选择 WebSocket 的原因：
- 前端需要实时接收 Agent 的多条消息（AI 文本、工具调用指令、结束信号）
- WebSocket 是唯一能同时支持双向实时通信的方案
- SSE 只能服务端→客户端，REST 轮询延迟太高

### 核心业务流程

以"创建卫星"为例：
1. 用户输入"我要创建卫星"
2. 智能体识别意图，发出创建卫星指令
3. 前端检查是否已创建场景
4. 如果没有场景 → 报异常并通知智能体
5. 如果有场景 → 创建卫星并返回结果

即：创建卫星轨迹、创建实体、查询实体及未来的数据分析都需要基于已创建的场景。

## 技术选型

| 类别 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.13 | 当前工程已创建的环境 |
| Web 框架 | FastAPI | 异步支持、自动文档、WebSocket 内建 |
| Agent Harness | deepagents | LangChain 团队开发的 Agent Harness，内置任务规划、子 Agent 生成、长期记忆 |
| Agent 运行时 | LangGraph | DeepAgent 底层运行时，支持持久化执行、流式输出、Human-in-the-Loop |
| LLM 接口 | langchain-openai | OpenAI 兼容接口，统一支持 DeepSeek 和阿里 DashScope（Qwen） |
| LLM 提供商 | DeepSeek + Qwen | 通过配置切换，开发用 DeepSeek，生产可能用 Qwen |
| 持久化 | SQLite | 开发阶段使用，后续可迁移 PostgreSQL |
| 配置管理 | YAML + .env | YAML 放业务配置，.env 放敏感信息（不提交 Git） |
| 日志 | structlog | 结构化 JSON 日志，控制台 + 文件轮转，可接入 ELK |
| 包管理 | pyproject.toml + requirements.txt | pyproject.toml 为现代标准，requirements.txt 供国内 CI/CD 使用 |
| 代码质量 | ruff | 一体化工具（格式化 + lint），用 Rust 编写，速度极快 |
| Git Hooks | pre-commit | 在 git commit 时自动运行 ruff 检查，防止不规范代码提交 |
| 测试 | pytest + pytest-asyncio | Python 社区最主流的测试框架 |

## 架构设计

### 多 Agent + Skill 渐进式披露

```
                        用户输入(WebSocket)
                              │
                              ▼
                    ┌─────────────────┐
                    │  Orchestrator   │  主控Agent：意图识别、任务规划
                    │  (DeepAgent)    │  只知道 Skill 摘要列表
                    └───────┬─────────┘
                            │ 路由到子Agent
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │  Scene   │  │  Entity  │  │ Analysis │
        │  Agent   │  │  Agent   │  │  Agent   │  ← 未来扩展
        └────┬─────┘  └────┬─────┘  └──────────┘
             │              │
     ┌───────┴──┐    ┌─────┴──────────┐
     │ Skill:   │    │ Skill:          │
     │ scene    │    │ entity          │
     │ manage   │    │ manage          │
     └──────────┘    ├─────────────────┤
                     │ Skill:          │
                     │ orbit           │
                     │ manage          │
                     └─────────────────┘
              │              │
              ▼              ▼
        Remote Tool Bridge (WebSocket)
              │              │
              ▼              ▼
         Cesium 前端执行
```

### 为什么用多 Agent

| Agent | 职责 | 原因 |
|-------|------|------|
| Orchestrator | 意图识别、任务规划、子 Agent 调度 | 降低单 Agent 复杂度，更准确路由 |
| Scene Agent | 场景创建/重命名/清除/查询 | 场景操作独立性高，有前置依赖检查 |
| Entity Agent | 实体创建/SGP4轨道/更新 | 实体和轨道操作紧密相关 |
| Analysis Agent | 数据分析（未来） | 独立领域，单独扩展 |

### 为什么用 Skill 渐进式披露

**传统方式：** 一次性把所有工具绑定给 Agent → 每次 LLM 调用都携带全部工具描述 → Token 浪费 + 工具选择容易出错。

**Skill 渐进式披露：** 工具按 Skill 组织，Agent 按需加载 → LLM 只看到当前任务相关的工具 → 更准确 + 更省 Token + 更易扩展。

当前 8 个工具时差异不大，但未来加入数据分析、链路计算等功能后（可能 30-50 个工具），渐进式披露的价值就很大。

### 场景依赖处理策略

采用 **Prompt 规则 + 前端校验兜底**：
- Orchestrator 的 system prompt 中明确规则："创建实体前必须确保场景已创建"
- 前端收到工具调用指令时也会检查场景状态，未创建则返回错误
- 双重保障，不依赖单一环节

### 远程工具桥接（核心设计）

由于工具实际在前端 Cesium 中执行（不是后端），需要 **asyncio.Future 桥接机制**：

```
Agent 调用工具 → bridge.send_tool_call() → WebSocket 发送指令到前端
                                                      ↓
Agent 得到结果 ← await Future ← bridge.resolve() ← WebSocket 收到前端结果
```

每次工具调用创建一个 Future，绑定到唯一的 `tool_call_id`。WebSocket 收到前端的 `tool_result` 时，根据 ID 找到对应 Future 并 resolve。

### WebSocket 消息协议

所有消息为 JSON 格式，通过 `type` 字段区分。

#### 前端 → 后端

**用户输入 (`user_input`)**
```json
{
  "type": "user_input",
  "thread_id": "abc-123",
  "content": "帮我创建一个场景",
  "message_id": "msg-001"
}
```

**工具执行结果 (`tool_result`)** — 前端执行完 Cesium 操作后返回
```json
{
  "type": "tool_result",
  "thread_id": "abc-123",
  "tool_func": "createScenario",
  "tool_call_id": "uuid-xxx",
  "args": {},
  "success": true,
  "message": "场景创建成功",
  "data": {"scenarioName": "测试场景"}
}
```

#### 后端 → 前端

**AI 文本回复 (`ai_message`)**
```json
{"type": "ai_message", "thread_id": "abc-123", "content": "好的，正在为您创建场景"}
```

**工具调用指令 (`tool_call`)** — 让前端执行 Cesium 操作
```json
{
  "type": "tool_call",
  "thread_id": "abc-123",
  "tool_func": "createScenario",
  "tool_func_args": {"name": "测试场景", "centralBody": "Earth"},
  "tool_call_id": "uuid-xxx",
  "message_id": ""
}
```

**对话结束 (`end`)**
```json
{"type": "end", "thread_id": "abc-123"}
```

**错误 (`error`)**
```json
{"type": "error", "thread_id": "abc-123", "message": "工具调用超时: createScenario"}
```

#### 交互时序

```
前端                          后端
  │  user_input ──────────→  │
  │  ←──── ai_message       │  Agent 思考中...
  │  ←──── tool_call        │  Agent 决定调工具
  │  tool_result ─────────→  │  前端执行 Cesium 操作
  │  ←──── ai_message       │  Agent 拿到结果，回复
  │  ←──── end              │  轮次结束
```

### 工具清单

前端需要实现以下 `tool_func` 对应的方法：

| 工具函数名 (`tool_func`) | 所属 Skill | 参数 (`tool_func_args`) | 说明 |
|--------------------------|-----------|------------------------|------|
| `createScenario` | scene_management | `{name, centralBody, startTime?, endTime?, description?}` | 创建场景 |
| `renameScenario` | scene_management | `{name}` | 重命名场景 |
| `clearScene` | scene_management | `{}` | 清除场景 |
| `clearEntities` | scene_management | `{}` | 清除所有实体 |
| `queryScenario` | scene_management | `{sceneName?}` | 查询场景信息 |
| `queryScenarioEntities` | scene_management | `{}` | 查询实体列表 |
| `addPointEntity` | entity_management | `{entityType, name, position: {longitude, latitude, height}, properties?}` | 添加点实体 |
| `createSGP4Orbit` | orbit_management | `{name, tles, start?, end?}` | 创建 SGP4 轨道 |
| `updateSGP4Orbit` | orbit_management | `{name, color?, glowPower?, taperPower?}` | 更新轨道样式 |

参数中 `?` 表示可选字段。`entityType` 支持的值: `place`, `target`, `facility`, `aircraft`, `missile`, `satellite`, `sensor`, `groundVehicle`, `ship`, `launchVehicle`, `lineTarget`, `areaTarget`。

## 环境配置策略

### 多环境支持

| 环境 | 配置文件 | 特点 |
|------|---------|------|
| dev | `config/dev.yaml` | DEBUG 日志、控制台可读格式、不写文件 |
| staging | `config/staging.yaml` | INFO 日志、JSON 格式、写文件 |
| prod | `config/prod.yaml` | WARNING 日志、JSON 格式、30 个备份文件 |

通过 `APP_ENV` 环境变量切换，YAML 中 `${VAR:default}` 语法引用环境变量。

### 敏感信息管理

- `.env` 文件存放 API Key 等敏感信息，已被 `.gitignore` 排除
- `.env.example` 作为模板提交到 Git，新开发者复制后填写实际值
- YAML 配置通过 `${LLM_API_KEY}` 引用 .env 中的值

### 包管理策略

- `pyproject.toml` 定义依赖（现代 Python 标准方式）
- `requirements.txt` 由 `scripts/gen_requirements.py` 从 pyproject.toml 自动生成
- 国内 CI/CD 流水线（Jenkins 等）习惯用 `pip install -r requirements.txt`，两者兼容

## 工具说明

### ruff（代码质量）

一体化工具，替代 black + isort + flake8 三件套：
- **格式化**（替代 black）：统一代码风格
- **import 排序**（替代 isort）：自动整理 import 顺序
- **语法检查**（替代 flake8）：发现潜在代码问题

国内越来越多团队在切换到 ruff，因为配置更简单、速度更快。

### pre-commit

本地 Git hook 管理器，在 `git commit` 时自动运行 ruff 检查。不是只有 GitHub 能用，任何 Git 平台（GitLab、Gitee）都适用。

### pytest

Python 社区（国内外）最主流的测试框架，没有争议。配合 `pytest-asyncio` 支持异步测试。

## DeepSeek 与 OpenAI 兼容接口

DeepSeek 提供了 OpenAI 兼容接口，意味着代码层面和用 OpenAI 没有区别：

```python
# 只需更换 base_url 和 api_key
from langchain_openai import ChatOpenAI

# DeepSeek
llm = ChatOpenAI(
    base_url="https://api.deepseek.com",
    api_key="your-deepseek-key",
    model="deepseek-chat",
)

# 阿里 DashScope (Qwen) — 同样兼容
llm = ChatOpenAI(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="your-dashscope-key",
    model="qwen-plus",
)
```

通过配置 `LLM_PROVIDER` 环境变量切换，无需改代码。

## 现有项目参考

- 原有 DEMO 代码：https://gitee.com/910922164/space-aiagent
- 前端交互代码：`/Users/caojianming/projects/gis/space2024/plugins/sceneAgent`（仅参考，不修改）
- 本项目定位：在原有 DEMO 基础上，升级为生产级架构，使用 DeepAgent harness

## 快速开始

```bash
# 1. 复制环境变量配置
cp .env.example .env
# 编辑 .env 填写实际的 API Key

# 2. 创建虚拟环境
python3.13 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 生成 requirements.txt
python scripts/gen_requirements.py

# 5. 安装 pre-commit hooks
pre-commit install

# 6. 启动开发服务器
python -m space_aiagent.main
```
