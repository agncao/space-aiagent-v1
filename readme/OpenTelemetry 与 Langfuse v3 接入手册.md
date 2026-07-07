# OpenTelemetry 与 Langfuse v3 接入手册

> 本手册以 **space-aiagent** 项目（航天分析平台智能助手，Python + FastAPI + DeepAgents）的真实代码为参照，系统讲解 OpenTelemetry 的概念体系、与 Langfuse / Prometheus / Grafana 等平台的关系，以及 Python 应用、Java 应用、Python AI Agent 三种场景下的接入落地。
>
> 全文目标：让一个从未接触过分布式追踪的工程师，读完就能在自己的项目里跑通「自托管 Langfuse v3 + 应用埋点 + Trace/Log 关联」的完整链路。

---

## 目录

### 第一部分　概念与定位

1. [什么是可观测性，它和监控有什么区别](#1-什么是可观测性它和监控有什么区别)
2. [可观测性的三大支柱（及 AI 系统的第四维）](#2-可观测性的三大支柱及-ai-系统的第四维)
3. [没有 OpenTelemetry 的世界：那些真实存在的痛点](#3-没有-opentelemetry-的世界那些真实存在的痛点)
4. [OpenTelemetry 是什么：一个规范、三个实现、一个协议](#4-opentelemetry-是什么一个规范三个实现一个协议)
5. [必须先吃透的 10 个核心概念](#5-必须先吃透的-10-个核心概念)
6. [OpenTelemetry 与 Langfuse / Prometheus / Grafana / Jaeger 的关系](#6-opentelemetry-与-langfuse--prometheus--grafana--jaeger-的关系)
7. [对接各平台时反复出现的概念](#7-对接各平台时反复出现的概念)

### 第二部分　Python 应用如何接入

8. [先回答三个最常见的疑问](#8-先回答三个最常见的疑问)
9. [需要安装哪些包](#9-需要安装哪些包)
10. [接入 Tracing：从最小可用到生产可用](#10-接入-tracing从最小可用到生产可用)
11. [接入 Log：两条路线与本项目的选择](#11-接入-log两条路线与本项目的选择)
12. [没有 Prometheus / 运维平台时怎么办](#12-没有-prometheus--运维平台时怎么办)

### 第三部分　Java 应用如何接入

13. [Java 接入的独特优势：javaagent 字节码注入](#13-java-接入的独特优势javaagent-字节码注入)
14. [Java 依赖与目录结构](#14-java-依赖与目录结构)
15. [路线 A：零代码自动 Instrumentation（推荐）](#15-路线-a零代码自动-instrumentation推荐)
16. [路线 B：手动 SDK 接入](#16-路线-b手动-sdk-接入)
17. [Java 接入 Log：MDC 自动注入 trace_id](#17-java-接入-logmdc-自动注入-trace_id)
18. [Spring Boot 接入示例](#18-spring-boot-接入示例)
19. [Python 与 Java 接入对比](#19-python-与-java-接入对比)

### 第四部分　Python AI Agent（本项目）Tracing 与 Log 全解析

20. [本项目的可观测性架构与数据流](#20-本项目的可观测性架构与数据流)
21. [自托管 Langfuse v3（Docker Compose 详解）](#21-自托管-langfuse-v3docker-compose-详解)
22. [代码逐文件剖析](#22-代码逐文件剖析)
23. [业务埋点：一棵真实的 Trace 树长什么样](#23-业务埋点一棵真实的-trace-树长什么样)
24. [Trace 与 Log 的关联：trace_id 是那根线](#24-trace-与-log-的关联trace_id-是那根线)
25. [enabled=false 时的零开销 NoOp 设计](#25-enabledfalse-时的零开销-noop-设计)
26. [常见问题、调优与故障排查](#26-常见问题调优与故障排查)
27. [从零复现的 Step-by-step 清单](#27-从零复现的-step-by-step-清单)

---

## 第一部分　概念与定位

### 1. 什么是可观测性，它和监控有什么区别

**监控（Monitoring）**告诉你系统「**出了什么问题**」：CPU 90%、错误率上升、P99 延迟 2 秒。它基于**预先定义的指标和阈值**，本质是「**已知未知**」——你知道要盯哪些数字，只是不知道它此刻是多少。

**可观测性（Observability）**则回答一个更深的问题：**「为什么会这样？」** 当一个从未预设过告警的奇怪现象发生时，你能否从外部行为推导出内部状态？它处理的是「**未知未知**」。

用一个本项目里的真实场景说明：

> 用户在航天场景里说「添加文昌地面站」，前端什么都没发生。
>
> - **监控视角**：你看到 WebSocket 错误率 +2%， orchestrator.llm 的 P99 飙到 8 秒。你知道「坏了」，但不知道为什么。
> - **可观测性视角**：你打开这一条请求的 Trace，看到 `ws.session → orchestrator.llm → orchestrator.task → subagent.llm`，发现子 Agent 在 `tool.addPointEntity` 之前被 `ToolValidationMiddleware` 短路返回了 `NO_SCENE`（因为 `current_scene_name` 为空），自动续接又走了一次 Flash LLM 路由分类……每一步的延迟、入参、返回码都挂在 Span 上。再拿这条 Trace 的 `trace_id` 去日志系统一搜，同一条 `trace_id` 的所有日志按时间线铺开，根因一目了然。

可观测性的核心是**关联**：把分散的信号（一次请求经过了哪些服务、每一步耗时、每一步打了什么日志、消耗了多少 token）用统一的 ID 串成一条故事线。OpenTelemetry 就是干这件事的工业标准。

### 2. 可观测性的三大支柱（及 AI 系统的第四维）

经典的可观测性有三根支柱：

| 支柱 | 回答的问题 | 数据形态 | 典型后端 |
|------|-----------|----------|----------|
| **Traces（分布式追踪）** | 「这次请求经过了哪些环节，每一步耗时多少，调用关系是什么？」 | 一棵有父子关系的 Span 树 | Jaeger / Tempo / **Langfuse** |
| **Metrics（指标）** | 「系统的聚合状态如何？QPS、错误率、P99、CPU/内存？」 | 时间序列数值（counter / gauge / histogram） | Prometheus |
| **Logs（日志）** | 「那一刻到底发生了什么细节？」 | 带时间戳的离散事件记录 | Loki / ELK / Fluentd |

三者**单独存在价值有限，关联起来才有杀伤力**。关联的钥匙就是 `trace_id`——同一个 `trace_id` 既能找到它的 Span 树（Traces），也能过滤出它的所有日志（Logs），还能在 Metrics 里找到它所属时段的聚合曲线。

**AI 系统多了第四维：模型归因（LLM 语义维度）**。传统 Traces 只记录「调了一个 HTTP 接口，耗时 800ms」。但 AI 应用里你最关心的是：

- 这次 LLM 调用用了哪个模型、输入 prompt 是什么、输出是什么
- 消耗了多少 token、花了多少钱
- temperature 多少、是否命中缓存
- 工具调用的入参和返回
- 整个 Agent 的思考链路（多轮 ReAct、子 Agent 委派）

这就是 **Langfuse** 的定位：它一方面是一个标准的 OTLP Traces 后端（吃 OpenTelemetry 协议数据），另一方面专门为 LLM/AI 场景做了 token 归因、prompt 回放、成本统计、模型评估。本项目选它的核心理由就在于此——**既要通用追踪，又要 AI 维度**，Langfuse 一个后端全包了。

### 3. 没有 OpenTelemetry 的世界：那些真实存在的痛点

理解 OpenTelemetry 解决了什么，最好的方式是想象它不存在。

**痛点 1：厂商锁定（Vendor Lock-in）**
你三年前选了 Datadog，在代码里埋了几百处 `datadog.tracer.start_span(...)`。今年公司要切到 Jaeger + 自建。所有埋点代码重写一遍。再过两年又要切……Instrumentation 和后端绑死，迁移成本极高。

**痛点 2：每个后端一套 API**
Jaeger 用 Jaeger client，Zipkin 用 Brave，Datadog 用 dd-trace，New Relic 用自己的 SDK。同一个 Python 函数，你想同时发两份追踪数据（灰度切换）都做不到。

**痛点 3：跨语言不一致**
Python 团队用 A 厂商 SDK，Java 团队用 B 厂商 SDK，前端 Node 用 C 厂商 SDK。同一个请求横跨三种语言，trace_id 根本无法传递，Span 树拼不起来。分布式追踪的「分布式」三个字名存实亡。

**痛点 4：Trace 和 Log 无法关联**
日志系统里一条日志报错「用户下单失败」，追踪系统里有一条 Span 显示「订单服务耗时 3 秒」。但你不知道它们是同一次请求。因为没有统一标准把 `trace_id` 注入到日志里。

**痛点 5：重复造轮子**
每个可观测后端都要自己写一套「如何拦截 HTTP 请求」「如何记录数据库调用」「如何注入 Kafka 消息头」的 instrumentation 库。全行业重复劳动。

**OpenTelemetry 的解法**：把「**生成信号**」（instrumentation API/SDK）和「**存储/展示信号**」（backend）彻底解耦。

```
应用代码只写一遍 OTel API  →  可以同时发给 Jaeger / Langfuse / Datadog / Prometheus ...
                          ↑
                   迁移后端只改 Exporter 配置，业务代码一行不动
```

它做到了一件关键的事：**Instrumentation 一次，后端随意换**。这彻底消除了厂商锁定。

### 4. OpenTelemetry 是什么：一个规范、三个实现、一个协议

很多人把 OpenTelemetry 理解成「一个库」，这不准确。它是一个 **CNCF 顶会项目**（Cloud Native Computing Foundation 的第二大项目，仅次于 Kubernetes），由 OpenTracing 和 OpenCensus 两个老项目合并而来。它的结构是：

```
OpenTelemetry（项目）
├── ① 规范（Specification）     ← 语言无关的约定：什么是 Span、什么是 Resource、OTLP 长什么样
├── ② API + SDK（各语言实现）    ← Python/Java/Go/JS/Rust/... 每种语言一套
├── ③ Collector（可选中转）      ← 一个用 Go 写的独立进程，做接收/处理/转发
└── ④ OTLP（协议）              ← 数据在网络上的传输格式（HTTP/gRPC）
```

**① 规范优先**。规范规定了「`service.name` 必须是 Resource 属性」「Span 必须有 trace_id/span_id/parent_span_id」「采样器接口长什么样」。无论哪种语言实现，都遵循同一份规范，所以跨语言 trace 才能拼起来。

**② API 与 SDK 分离（关键设计）**：

- **API 层**：`opentelemetry-api`，只定义接口（`tracer.start_span()` 等），**不含任何实现**。业务代码只依赖 API。
- **SDK 层**：`opentelemetry-sdk`，提供真正的实现（TracerProvider、SpanProcessor、Exporter）。

为什么要分开？因为这样你可以**把第三方库的依赖里也写满 OTel API 调用**（库代码只 import api，不强制 SDK），而最终是否真的产生 Span、发到哪里，由**应用方**在启动时挂哪个 SDK 决定。库代码零侵入、零依赖、可移植。

本项目里你会看到这个分离的体现：业务文件里写的是 `from opentelemetry import trace`（API），而 TracerProvider 的组装在 `tracing.py` 里（SDK）。

**③ Collector 是可选的**。它是数据中转站：接收（receiver）→ 处理（processor，如采样、脱敏、批量）→ 转发（exporter）。它**不是后端，不存储数据，不提供 UI**。小项目可以不要 Collector，应用直接发后端（本项目就是这样，直接发 Langfuse）。大规模部署才用 Collector 做汇聚、重试、负载均衡。

**④ OTLP（OpenTelemetry Protocol）**是数据上线的标准格式。它有 HTTP（`/v1/traces`、`/v1/metrics`、`/v1/logs`）和 gRPC 两种传输。**任何后端只要实现了 OTLP 接收端点，就能吃 OTel 数据**——这正是 Langfuse 的 `/api/public/otel` 干的事。

### 5. 必须先吃透的 10 个核心概念

这 10 个概念贯穿本文剩余部分，先建立准确的心智模型。

**① Trace（一次追踪）**
一个请求/一次任务从头到尾的完整执行路径。用唯一的 `trace_id`（128 bit）标识。本质是「**一棵 Span 树**」。

**② Span（一段工作）**
Trace 里的一个节点，代表一段时间内的一项工作。用 `span_id`（64 bit）标识。Span 有：
- **起止时间**（start / end → 算出 duration）
- **名字**（如 `orchestrator.llm`）
- **属性（Attributes）**：键值对，如 `tool.name=createScenario`、`llm.latency_ms=820`
- **状态（Status）**：`UNSET` / `OK` / `ERROR`
- **事件（Events）**：Span 内的瞬时时间戳标记（如异常栈）
- **父 Span 指针**：`parent_span_id`，构成树形结构

**③ SpanContext**
Span 的「身份证」，包含 `trace_id`、`span_id`、`trace_flags`（采样位）、`is_remote`。**跨进程传递时传的就是它**（通过 HTTP Header / 消息头注入 W3C TraceContext）。

**④ Resource（资源）**
描述「**这些 Span 是谁产生的**」的固定属性，整个 TracerProvider 共享。最关键的是 `service.name`（如本项目的 `space-aiagent`），还有 `service.version`、`host.name`、`deployment.environment` 等。后端按 service 名做分组和过滤。

**⑤ TracerProvider**
全局工厂，负责创建 Tracer、持有 Resource 和 Sampler。一个进程通常只有一个。本项目里 `trace.set_tracer_provider(provider)` 设置它。

**⑥ Sampler（采样器）**
决定「**这个 Span 要不要真的记录并发送**」。全量上报在高流量下既贵又 noisy，所以采样。本项目用 `TraceIdRatioBased(sampler_ratio)`——按 trace_id 的哈希概率采样，`sampler_ratio=1.0` 即全量。生产可降到 0.1。

**⑦ SpanProcessor**
Span 结束后由谁处理。两类：
- **BatchSpanProcessor**（生产推荐）：批量攒队列、后台线程异步上报，性能好
- **SimpleSpanProcessor**：同步上报，每条都阻塞，仅用于调试

本项目通过 Langfuse SDK 自动挂载 `LangfuseSpanProcessor`（内部是 Batch 语义）。

**⑧ Exporter（导出器）**
把 Span 真正发到后端的组件，发的是 OTLP 协议。`OTLPSpanExporter` 是通用 OTLP 导出器，可发任何 OTLP 后端。本项目里 Langfuse SDK 自带了能直发 Langfuse 的 Exporter。

**⑨ Context Propagation（上下文传播）**
**分布式追踪的命脉**。当前活跃的 Span 是怎么「记住」的？怎么跨线程、跨进程、跨服务传递？

- 进程内：OTel 用 `context`（Python 里基于 `contextvars`）维护「当前 Span」。`start_as_current_span` 把 Span 压栈，退出时弹栈。
- 跨进程：发起 HTTP 调用时，OTel 的 instrumentor 自动把当前 SpanContext 注入到请求头（W3C `traceparent` 头）；对端收到后从头部解析，作为自己 Span 的 parent。于是两段独立的 Trace 拼成了一条。

> ⚠️ **本项目的特别注意**：本项目用 LangGraph，每个 graph node 在独立的 `copy_context()` + `asyncio.create_task` 里运行，**ContextVar 跨 node 边界默认会丢失**。这也是为什么项目里 `current_scene_name` 要走 `SpaceAgentState` 而不是 ContextVar。但 OTel 的 trace context 同理——好在 LangGraph 在 node 边界会拷贝 context，trace_id 一般能续上；如果发现子 Agent 的 Span 掉链子，多半就是 context 传播断了，可以用 `contextvars.copy_context()` 手动接力。

**⑩ Instrumentation（插桩）**
「让代码自动产生 Span」的机制。两种：
- **自动 Instrumentation**：装一个 `opentelemetry-instrumentation-fastapi` 这样的包，一行代码给所有 HTTP 请求、数据库调用自动加 Span。本项目用了 `FastAPIInstrumentor.instrument_app(app)`。
- **手动 Instrumentation**：业务代码里显式 `with tracer.start_as_current_span("orchestrator.llm"):`。本项目业务埋点都是手动的。

---

### 6. OpenTelemetry 与 Langfuse / Prometheus / Grafana / Jaeger 的关系

记住一句话：**OpenTelemetry 生成数据，平台存储并展示数据。** OpenTelemetry 自己**不存储、不查询、不展示**。

下面是它们的位置关系图：

```
              你的应用 (Python / Java / Node / Go / ...)
              ┌─────────────────────────────────────────┐
              │   OpenTelemetry Instrumentation         │
              │   (API + SDK，生成 Traces/Metrics/Logs， │
              │    统一 OTLP 格式)                       │
              └──────────────────┬──────────────────────┘
                                 │ OTLP (HTTP / gRPC)
                                 ▼
              ┌─────────────────────────────────────────┐
              │   OTel Collector（可选中转）             │  ← 可省略：应用直发后端
              │   receiver → processor → exporter       │
              └───┬──────────────┬──────────────┬───────┘
                  │              │              │
       ┌──────────┘              │              └──────────┐
       ▼                         ▼                         ▼
 ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
 │  Traces 后端 │         │ Metrics 后端 │         │  Logs 后端   │
 ├──────────────┤         ├──────────────┤         ├──────────────┤
 │ Langfuse ★   │         │ Prometheus   │         │ Loki / ELK   │
 │ Jaeger       │         │              │         │              │
 │ Tempo        │         │              │         │              │
 │ DataDog ...  │         │              │         │              │
 └──────┬───────┘         └──────┬───────┘         └──────┬───────┘
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 ▼
                       ┌──────────────────┐
                       │ Grafana / 各自 UI│  ← 可视化层（数据源聚合）
                       └──────────────────┘
```

逐个澄清它们的角色：

**Langfuse —— AI 维度的 Traces 后端（本项目用）**
- 它实现了 OTLP 接收端点（`/api/public/otel`），所以是**合法的 OpenTelemetry Traces 后端**。
- 它**只吃 Traces**（不吃 Metrics，Logs 也只支持 score/annotation 维度，不是通用日志后端）。
- 它在通用追踪之上，**专门为 LLM 场景**做了：prompt/completion 回放、token & cost 归因、模型评估、人工标注。这是它和 Jaeger/Tempo 的本质区别。
- 它可以**自托管**（开源，AGPL），本项目就是 Docker 自托管 v3。

**Prometheus —— Metrics 后端（和 Traces 无关）**
- 只处理 Metrics（时间序列数值），**不接收 Traces，也不接收 Logs**。
- 它是 **pull 模型**：Prometheus 主动去抓你的 `/metrics` 端点（`/metrics` 暴露文本格式指标）。这和 OTel 的 push 模型不同。
- **OTel Metrics 怎么进 Prometheus？** 两种方式：(a) OTel Collector 配 `prometheus` exporter，Collector 起一个 `/metrics` 端点让 Prometheus 抓；(b) 用 `prometheusremotewrite` exporter 直接 push 给支持 remote write 的 Prometheus。所以「OpenTelemetry 替代 Prometheus」是**错误**的——它们是**配合**关系，OTel 是数据源，Prometheus 是存储。

**Grafana —— 可视化层（不存数据）**
- Grafana **本身不存储任何可观测数据**，它只是一个仪表盘，通过「数据源插件」连接到 Prometheus（看 Metrics）、Loki（看 Logs）、Jaeger/Tempo/Langfuse（看 Traces）。
- 一个 Grafana 面板上可以同时下钻 Metrics → Logs → Traces，靠的就是统一 `trace_id`。

**Jaeger / Tempo —— 通用 Traces 后端**
- 和 Langfuse 同生态位（都吃 OTLP Traces），但**没有 LLM 归因能力**。如果你做的是非 AI 应用，Jaeger/Tempo 就够；做 AI 应用，Langfuse 更合适。本项目两者其实可以并存（Collector 同时发给两个），但当前只接 Langfuse。

**Loki / ELK —— Logs 后端**
- 存日志、搜日志。它们**不主动关联 Traces**，关联靠你在日志里带上 `trace_id` 字段，然后手动用 `trace_id` 检索。这正是本项目 `add_trace_info` processor 干的事。

**一句话总结**：OpenTelemetry 是「**数据的生产者和标准**」，Langfuse/Prometheus/Jaeger/Loki 是「**数据的仓库**」，Grafana 是「**数据的展柜**」。三者分工，用 OTLP 和 `trace_id` 串起来。

### 7. 对接各平台时反复出现的概念

无论你对接哪个后端，下面这些概念都会出现，提前记住能少走很多弯路。

| 概念 | 含义 | 本项目的取值 |
|------|------|-------------|
| **OTLP Endpoint** | 后端接收 OTLP 数据的 URL。Traces 一般是 `/v1/traces`（标准）或自定义（如 Langfuse 的 `/api/public/otel`） | `http://localhost:3000/api/public/otel` |
| **Service Name** | 你的应用的标识，Resource 必填项 | `space-aiagent` |
| **Credentials / Headers** | 后端鉴权。Langfuse 用 public/secret key（Basic Auth 或 `x-langfuse-public-key`/`x-langfuse-secret-key` 头） | `.env` 里 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` |
| **Sampling Ratio** | 采样率，0.0~1.0 | `1.0`（dev 全量），生产可 0.1 |
| **Batch / Flush** | 批量上报参数（队列大小、刷盘间隔、超时） | Langfuse SDK 默认，可用 `LANGFUSE_FLUSH_AT` / `LANGFUSE_FLUSH_INTERVAL` 调 |
| **Trace Context Propagation** | 跨进程传 trace_id 的机制，标准是 W3C TraceContext（`traceparent` 头） | OTel SDK 默认启用 |
| **Semantic Conventions** | 官方推荐的属性命名约定（如 `http.method`、`db.system`、`gen_ai.system`），方便后端识别 | 本项目自定义了 `llm.latency_ms`、`tool.name` 等 |
| **Collector Pipeline** | Collector 内部 `receiver → processor → exporter` 三段式 | 本项目无 Collector，直发 |

特别注意 AI 场景的 **Semantic Conventions for GenAI**（`gen_ai.*`）：OTel 社区正在标准化 LLM 调用的属性命名（`gen_ai.system`、`gen_ai.request.model`、`gen_ai.usage.prompt_tokens` 等）。Langfuse SDK 会自动把这些归因属性附加到 Span 上，所以你在 Langfuse UI 能看到 token 用量——这部分**不需要你手写**，Langfuse 的 LangChain/OpenAI 集成会兜底（本项目未来开 LLM 自动归因时会启用）。

---

## 第二部分　Python 应用如何接入

### 8. 先回答三个最常见的疑问

**疑问 1：我是否需要独立部署一个 OpenTelemetry 平台或代理？**

**不需要部署「OpenTelemetry 平台」——因为根本不存在这个东西。** OpenTelemetry 项目里没有任何「存储/查询/UI」组件，它只是 SDK + Collector + 协议。你需要部署的是一个**后端**（Langfuse / Jaeger / Tempo 之一）。

至于 **Collector（代理）**：小项目可以不要，应用直接发后端；大项目部署 Collector 做汇聚更合理。**本项目就是「无 Collector，直发 Langfuse」**，所以你的部署清单里只有一个 Langfuse（用 Docker Compose 起 6 个容器）。

**疑问 2：我是否需要装什么开发包？**

需要。最小集合是三个：
- `opentelemetry-api`（接口）
- `opentelemetry-sdk`（实现）
- `opentelemetry-exporter-otlp-proto-http`（OTLP HTTP 导出器）

加上你用到的自动 instrumentation（如 `opentelemetry-instrumentation-fastapi`）和后端 SDK（如 `langfuse`）。完整清单见 [§9](#9-需要安装哪些包)。

**疑问 3：如果没有 Prometheus 或者任何运维平台，光接入 tracing/log，OpenTelemetry 自己提供查看数据的地方吗？**

**不提供。** 这是最容易踩的认知坑。OpenTelemetry **没有任何 UI、没有任何数据库、没有任何查询接口**。你写完 `tracer.start_span(...)` 之后，如果不去配 Exporter 指向一个后端，这些 Span 就在内存里生成、然后被丢弃，你**什么都看不到**。

所以完整的可观测性闭环必须是：

```
应用埋点（OTel SDK）  +  一个后端（存数据 + 给 UI）  =  能看到数据
```

**没有后端 = 接了等于没接。** 这正是本项目要自托管 Langfuse 的根本原因——Langfuse 就是那个「存数据 + 给 UI」的后端。下面的 [§12](#12-没有-prometheus--运维平台时怎么办) 会专门讲没有现成平台时怎么办。

### 9. 需要安装哪些包

**核心三件套**（任何 OTel Python 项目都要）：

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

**自动 Instrumentation**（按需，对应你用的库）：

```bash
# Web 框架
pip install opentelemetry-instrumentation-fastapi
pip install opentelemetry-instrumentation-flask
pip install opentelemetry-instrumentation-django

# HTTP 客户端
pip install opentelemetry-instrumentation-requests
pip install opentelemetry-instrumentation-httpx

# 数据库
pip install opentelemetry-instrumentation-sqlalchemy
pip install opentelemetry-instrumentation-asyncpg

# 日志（trace_id 注入）
pip install opentelemetry-instrumentation-logging
```

**后端 SDK**（按你选的后端）：

```bash
# 用 Langfuse（AI 场景）
pip install "langfuse>=3.0,<4.0"
```

本项目的 `pyproject.toml` 里实际装的是：

```toml
"opentelemetry-api>=1.27.0",
"opentelemetry-sdk>=1.27.0",
"opentelemetry-instrumentation-fastapi>=0.48b.0",
"langfuse>=3.0,<4.0",
```

注意没有显式装 `opentelemetry-exporter-otlp-proto-http`——因为 Langfuse SDK 内部已经带了能发 OTLP 给 Langfuse 的导出器，不需要通用 OTLP exporter。如果你想发给 Jaeger/Tempo 等通用后端，才需要装它。

### 10. 接入 Tracing：从最小可用到生产可用

#### 10.1 五步法

不管后端是 Langfuse 还是别的，Python 接入 Tracing 永远是这五步：

```
① 创建 Resource（声明 service.name）
② 创建 TracerProvider（装上 Resource + Sampler）
③ 创建 Exporter（指向后端）+ SpanProcessor（批量策略）→ 挂到 Provider
④ set_tracer_provider(provider)（设为全局）
⑤ 业务代码 get_tracer(__name__).start_as_current_span("...")
```

#### 10.2 最小可用示例（通用 OTLP，发任意后端）

先给一个**不依赖 Langfuse** 的纯 OTel 示例，让你看清本质。它能发给任何 OTLP 后端（Jaeger、Tempo、Langfuse、自建 Collector 都行）：

```python
# telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, sampling
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

def setup_telemetry() -> None:
    # ① Resource：声明"我是谁"
    resource = Resource.create({
        "service.name": "my-python-service",          # 必填
        "service.version": "1.0.0",
        "deployment.environment": "dev",
    })

    # ② TracerProvider + 采样器
    provider = TracerProvider(
        resource=resource,
        sampler=sampling.ParentBased(sampling.TraceIdRatioBased(1.0)),  # 全量采样
    )

    # ③ Exporter + SpanProcessor
    #    endpoint 指向你的后端 OTLP 接收地址；标准后端用 /v1/traces
    exporter = OTLPSpanExporter(
        endpoint="http://localhost:4318/v1/traces",   # Collector 或任意 OTLP 后端
        # headers={"Authorization": "Basic <base64>"},  # 需要鉴权时加
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # ④ 设为全局
    trace.set_tracer_provider(provider)


def shutdown_telemetry() -> None:
    """进程退出前 flush，否则批队列里的 Span 会丢。"""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
```

业务代码用法：

```python
# app.py
from opentelemetry import trace
from telemetry import setup_telemetry, shutdown_telemetry

setup_telemetry()
tracer = trace.get_tracer(__name__)

def handle_request(user_id: str) -> str:
    # ⑤ 手动埋点：start_as_current_span 自动管理 parent 关系
    with tracer.start_as_current_span("handle_request") as span:
        span.set_attribute("user.id", user_id)        # 挂属性

        result = do_something(user_id)
        if not result:
            span.set_status(trace.Status(trace.StatusCode.ERROR, "empty result"))

        span.set_attribute("result.length", len(result))
        return result
```

**关键点**：`start_as_current_span` 是个 context manager，进入时把 Span 压进 current context，退出时自动 end。**嵌套调用时，内层 Span 自动把外层作为 parent**——这就是 Span 树自然形成的方式，你不用手动指定 parent。

#### 10.3 自动 Instrumentation：让框架调用自动产 Span

手动埋点只覆盖你自己写的代码。但 HTTP 请求进/出、数据库查询、下游调用这些，每个都手写太累。自动 instrumentation 帮你拦截。

FastAPI 的例子（本项目 `main.py` 就是这么做的）：

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

app = FastAPI()
# 一行代码：给每个 HTTP 请求自动建一个 server span，包含路由、状态码、耗时
FastAPIInstrumentor.instrument_app(app, excluded_urls="/health")
```

这样每个进来的 HTTP 请求会自动有一个 `GET /foo` 这样的 Span，**还自动从请求头解析 W3C `traceparent`**，如果上游服务传了 trace_id，就接上形成跨服务链路。

#### 10.4 生产可用：补上优雅关闭

生产里最容易踩的坑是**进程退出时批队列没 flush，最后几秒的 Span 丢了**。两个对策：

1. 注册 `atexit` 或在框架生命周期里调 `provider.shutdown()`（本项目在 FastAPI `lifespan` 的 finally 里调 `shutdown_telemetry()`）。
2. 用 `BatchSpanProcessor` 而不是 `SimpleSpanProcessor`（异步、高性能、自动重试）。

本项目 `main.py` 的 lifespan 写法：

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_telemetry(settings)      # 启动期初始化
    try:
        yield
    finally:
        shutdown_telemetry()       # 关闭期 flush，避免 trace 丢失

app = FastAPI(lifespan=lifespan)
```

### 11. 接入 Log：两条路线与本项目的选择

日志接入有**两条完全不同的路线**，理解它们的区别很重要，因为很多新手会混淆。

#### 路线 A：trace_id 注入到应用日志（推荐，本项目用）

**思路**：你的日志系统不变（structlog / logging / loguru 照常用），只是在**每条日志里多加一个 `trace_id` 字段**。这样在 Loki/ELK 里搜 `trace_id=xxx` 就能找到这次请求的所有日志，并和 Langfuse 里的 Span 树对应起来。

**这是 OpenTelemetry 官方文档里推荐的「Logs and Traces Correlation」做法**，因为：
- 你已有的日志管线（格式化、轮转、采集、检索）完全不动
- 实现极简，性能开销几乎为零
- Loki/ELK 本来就是为日志检索优化的，比把日志塞进 trace 后端更合理

**实现核心**：从当前 OTel Span 取 `trace_id`，塞进日志记录。本项目的 `processors.py` 就是这个：

```python
# infrastructure/observability/processors.py
from opentelemetry import trace

def add_trace_info(_, __, event_dict):
    """structlog processor：把当前 span 的 trace_id/span_id 注入每条日志"""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:                              # 无 active span 时跳过（零开销）
        event_dict["trace_id"] = f"{ctx.trace_id:032x}"   # 128bit → 32 位十六进制
        event_dict["span_id"] = f"{ctx.span_id:016x}"     # 64bit → 16 位十六进制
    return event_dict
```

然后在 structlog 的 processor 链里加上它（本项目 `logging.py`）：

```python
shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    # ...
    _add_trace_info,   # ← 关键：每条日志都带上当前 trace_id
    structlog.processors.TimeStamper(fmt="iso"),
    # ...
]
```

**不用 structlog 怎么办？** 用标准库 logging 也行，给 Formatter 加上从 `trace.get_current_span()` 取 trace_id 的逻辑。或者直接装 `opentelemetry-instrumentation-logging`：

```python
from opentelemetry.instrumentation.logging import LoggingInstrumentor
LoggingInstrumentor().instrument(set_logging_format=True)
# 它会自动给每条 LogRecord 注入 trace_id/span_id（通过 logging.LogRecord 的 makeRecord 钩子）
# 然后你的 formatter 里写 %(trace_id)s %(span_id)s 即可
```

#### 路线 B：OTel Logs（把日志当 OTLP 数据发后端）

**思路**：把日志记录转成 OTel 的 `LogRecord`，通过 `LogRecordExporter` 走 OTLP `/v1/logs` 发给支持 OTLP Logs 的后端（如 Loki via Collector、OTel-native 的日志后端）。

这条路是 OTel 的「Logs 信号」正式方案，但**目前成熟度不如 Traces/Metrics**，且要求你的后端支持 OTLP Logs（很多老牌日志系统不支持，要通过 Collector 转译）。除非你要建一套纯 OTel 三支柱统一管线，否则**不推荐**为了它重构现有日志。

```python
# 概念示例（OTel Logs，本项目未采用）
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint="http://localhost:4318/v1/logs"))
)
set_logger_provider(logger_provider)

# 把 stdlib logging 接到 OTel Logs
handler = LoggingHandler(logger_provider=logger_provider)
logging.getLogger().addHandler(handler)
```

#### 本项目为什么选路线 A

| 维度 | 路线 A（trace_id 注入） | 路线 B（OTel Logs） |
|------|----------------------|-------------------|
| 改造成本 | 极低（加一个 structlog processor） | 高（重构日志管线） |
| 后端要求 | 任意日志后端（Loki/ELK/文件） | 必须支持 OTLP Logs |
| 检索体验 | 在 Loki/ELK 搜 trace_id | 在 OTel Logs 后端搜 |
| 成熟度 | 生产验证多年 | 较新，生态待完善 |
| 性能 | 几乎为零 | 多一条 OTLP 上报链路 |

本项目已有的 structlog + Spring 风格日志体系已经很成熟（[见 `python教程.md` §6](./python教程.md)），路线 A 顺理成章：**Langfuse 看 Trace 树，Loki/ELK 看 trace_id 关联的日志**，各司其职。

### 12. 没有 Prometheus / 运维平台时怎么办

回到那个关键认知：**OpenTelemetry 不提供后端，你必须有一个后端才能看到数据。**

如果你公司没有 Prometheus、没有 Jaeger、没有 ELK、没有任何现成的可观测平台，你有三条路：

**路 1：用 SaaS（最快，但要花钱 + 数据出境）**
直接用 Langfuse Cloud / Honeycomb / Datadog / Tempo Cloud。注册账号拿 endpoint + key，应用 Exporter 指过去就行。缺点：数据出境合规、长期成本、AI 场景的 prompt 可能含敏感业务数据。

**路 2：自托管一个开源后端（推荐）**
- AI 应用 → 自托管 **Langfuse**（本项目选择）
- 通用 Traces → 自托管 Jaeger 或 Grafana Tempo
- Metrics → 自托管 Prometheus
- Logs → 自托管 Loki 或 ELK
- 全家桶 → Grafana 做统一面板

自托管的成本是一台 Docker 主机，数据完全在内网。**本项目的 Langfuse v3 自托管只需要一条 `docker compose up -d`**（见 [§21](#21-自托管-langfuse-v3docker-compose-详解)）。

**路 3：先用 OTel Collector 把数据收着，后端以后再说**
部署一个 Collector，先把应用数据发到 Collector，Collector 先落盘或暂存，后端建好之后再接。适合「先标准化埋点，后端分期建设」的团队。

**本项目的选择是「路 2」**：自托管 Langfuse v3，零外发、零 SaaS 依赖、AI 维度全包。下一部分讲 Java，第四部分会手把手讲这个自托管。

---

## 第三部分　Java 应用如何接入

### 13. Java 接入的独特优势：javaagent 字节码注入

在讲具体步骤前，必须先说清楚 Java 接入 OTel 相比 Python 的一个**巨大优势**：**javaagent 字节码自动注入**。

Python/Node/Go 的自动 instrumentation 是「在你的代码里调一行 `instrument_app(app)`」——你要改代码、要知道用什么库。

Java 不一样。JVM 启动时可以挂一个 `-javaagent:opentelemetry-javaagent.jar`，这个 agent 会在类加载时**改写字节码**，自动给几百个主流库（Spring MVC、HTTPClient、JDBC、Kafka、Redis、gRPC、Logback……）加 Span。**你的业务代码一行都不用改，启动命令加一个参数，全链路追踪就有了。**

这是 Java 生态的「杀手锏」，也是为什么 Java 的 OTel 接入往往比 Python 更简单。

```
# 启动时挂 agent（零代码改动）
java -javaagent:opentelemetry-javaagent.jar \
     -Dotel.service.name=my-java-service \
     -Dotel.exporter.otlp.endpoint=http://localhost:4318 \
     -jar my-app.jar
```

### 14. Java 依赖与目录结构

**方式 A：纯 javaagent（推荐，零代码）**
不需要在项目里加任何 OTel 依赖，只要下载一个 agent jar：

```bash
# 下载最新版（约 30MB）
curl -L -o opentelemetry-javaagent.jar \
  https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar
```

**方式 B：手动 SDK（需要细粒度控制时）**

Maven：

```xml
<dependencies>
    <!-- API（业务代码只依赖 API） -->
    <dependency>
        <groupId>io.opentelemetry</groupId>
        <artifactId>opentelemetry-api</artifactId>
        <version>1.39.0</version>
    </dependency>
    <!-- SDK（应用启动时引入） -->
    <dependency>
        <groupId>io.opentelemetry</groupId>
        <artifactId>opentelemetry-sdk</artifactId>
        <version>1.39.0</version>
    </dependency>
    <!-- OTLP 导出器 -->
    <dependency>
        <groupId>io.opentelemetry</groupId>
        <artifactId>opentelemetry-exporter-otlp</artifactId>
        <version>1.39.0</version>
    </dependency>
</dependencies>
```

Gradle：

```groovy
implementation 'io.opentelemetry:opentelemetry-api:1.39.0'
implementation 'io.opentelemetry:opentelemetry-sdk:1.39.0'
implementation 'io.opentelemetry:opentelemetry-exporter-otlp:1.39.0'
```

### 15. 路线 A：零代码自动 Instrumentation（推荐）

这是 95% 的 Java 项目该走的路。

**步骤 1**：下载 agent jar（见 §14）。

**步骤 2**：启动时挂上 agent，用 `-D` 或环境变量配置：

```bash
java -javaagent:opentelemetry-javaagent.jar \
     -Dotel.service.name=order-service \
     -Dotel.service.version=1.0.0 \
     -Dotel.exporter.otlp.endpoint=http://langfuse:3000/api/public/otel \
     -Dotel.exporter.otlp.headers="Authorization=Basic <base64(public:secret)>" \
     -Dotel.traces.sampler=parentbased_traceidratio \
     -Dotel.traces.sampler.arg=1.0 \
     -jar order-service.jar
```

或者用环境变量（更适合容器化）：

```bash
export OTEL_SERVICE_NAME=order-service
export OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(public:secret)>"
export OTEL_TRACES_SAMPLER=parentbased_traceidratio
export OTEL_TRACES_SAMPLER_ARG=1.0
java -javaagent:opentelemetry-javaagent.jar -jar order-service.jar
```

**就这样**。你的每个 HTTP 请求、每次数据库查询、每条 Kafka 消息、每个 Redis 调用，都会自动产生 Span，发到你配的后端。

**关键配置项**：

| 配置 | 含义 |
|------|------|
| `OTEL_SERVICE_NAME` | Resource 的 service.name（必填） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 后端 OTLP 地址 |
| `OTEL_EXPORTER_OTLP_HEADERS` | 鉴权头（key=value 格式，多个用逗号） |
| `OTEL_TRACES_SAMPLER` | 采样器，常用 `parentbased_traceidratio` |
| `OTEL_TRACES_SAMPLER_ARG` | 采样率，0.0~1.0 |
| `OTEL_PROPAGATORS` | 上下文传播格式，默认 `tracecontext,baggage` |
| `OTEL_INSTRUMENTATION_<NAME>_ENABLED` | 单独开关某个 instrumentation |

### 16. 路线 B：手动 SDK 接入

当你需要细粒度控制（自定义 Span、自定义 Resource、和现有框架深度集成）时走这条。

```java
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.BatchSpanProcessor;
import io.opentelemetry.sdk.trace.samplers.Sampler;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.semconv.ServiceAttributes;

public class TelemetryConfig {
    private static OpenTelemetry openTelemetry;

    public static void init() {
        // ① Resource
        Resource resource = Resource.getDefault()
            .merge(Resource.create(java.util.Map.of(
                ServiceAttributes.SERVICE_NAME.getKey(), "order-service",
                "service.version", "1.0.0"
            )));

        // ② Exporter（OTLP HTTP，指向后端）
        OtlpHttpSpanExporter exporter = OtlpHttpSpanExporter.builder()
            .setEndpoint("http://localhost:4318/v1/traces")
            // .addHeader("Authorization", "Basic <base64>")  // 需要鉴权时
            .build();

        // ③ TracerProvider + Sampler + SpanProcessor
        SdkTracerProvider provider = SdkTracerProvider.builder()
            .setResource(resource)
            .setSampler(Sampler.parentBased(Sampler.traceIdRatioBased(1.0)))
            .addSpanProcessor(BatchSpanProcessor.builder(exporter).build())
            .build();

        // ④ 注册全局 + JVM 钩子优雅关闭
        OpenTelemetrySdk sdk = OpenTelemetrySdk.builder()
            .setTracerProvider(provider)
            .build();
        Runtime.getRuntime().addShutdownHook(new Thread(provider::close));  // ★ flush

        openTelemetry = sdk;
    }

    public static Tracer tracer() {
        return openTelemetry.getTracer("order-service");
    }
}
```

业务代码：

```java
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.GlobalOpenTelemetry;

public class OrderService {
    private static final Tracer tracer = GlobalOpenTelemetry.getTracer("order-service");

    public String createOrder(String userId) {
        Span span = tracer.spanBuilder("createOrder")
            .setAttribute("user.id", userId)
            .startSpan();
        try (var scope = span.makeCurrent()) {   // 等价于 Python 的 start_as_current_span
            String result = doWork(userId);
            span.setAttribute("result.length", result.length());
            return result;
        } catch (Exception e) {
            span.recordException(e);
            span.setStatus(StatusCode.ERROR);
            throw e;
        } finally {
            span.end();   // ★ 必须 end，否则 Span 永不结束
        }
    }
}
```

注意和 Python 的对比：Python 的 `with start_as_current_span(...)` 退出时**自动 end**；Java 没有 try-with-resources 自动 end 的官方写法（有 `AutoCloseable` 的社区封装），所以**记得在 finally 里 `span.end()`**，这是 Java 新手最常忘的。

### 17. Java 接入 Log：MDC 自动注入 trace_id

Java 的日志（Logback / Log4j2）有 **MDC（Mapped Diagnostic Context）**，类似 Python 的 `contextvars`。OTel javaagent 会**自动把当前 Span 的 trace_id/span_id 塞进 MDC**，你只要在日志格式里引用即可。

**Logback 配置示例**（`logback-spring.xml`）：

```xml
<configuration>
    <appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <!-- %X{trace_id} %X{span_id} 就是从 MDC 取，由 OTel agent 自动注入 -->
            <pattern>%d{yyyy-MM-dd HH:mm:ss} [%thread] trace_id=%X{trace_id} span_id=%X{span_id} %-5level %logger{36} - %msg%n</pattern>
        </encoder>
    </appender>
    <root level="INFO">
        <appender-ref ref="STDOUT"/>
    </root>
</configuration>
```

**输出效果**：

```
2026-07-06 14:30:22 [http-nio-8080-exec-1] trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7 INFO  OrderService - 创建订单 user=123
```

拿着这个 `trace_id` 就能在 Langfuse / Jaeger 里反查同一条链路。**和本项目 Python 的 `add_trace_info` processor 是完全对称的设计**——只不过 Python 用 contextvars + structlog processor，Java 用 MDC + Logback pattern。

> 注意：自动 MDC 注入**只在挂了 javaagent 时才生效**。手动 SDK 路线需要用 `io.opentelemetry:opentelemetry-instrumentation-logging` 或自己写 servlet filter 往 MDC 塞 trace_id。

### 18. Spring Boot 接入示例

Spring Boot 项目最省心的组合是 **javaagent + application.yml**。

**`application.yml`**（和本项目的 `application.yaml` 思路一致）：

```yaml
# 业务的日志配置（带 trace_id）
logging:
  pattern:
    console: "%d{yyyy-MM-dd HH:mm:ss} trace_id=%X{trace_id} span_id=%X{span_id} %-5level %logger - %msg%n"
  level:
    com.example: DEBUG
    org.springframework: INFO
```

**`Dockerfile`**（容器化挂 agent）：

```dockerfile
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY target/order-service.jar app.jar
COPY opentelemetry-javaagent.jar opentelemetry-javaagent.jar
ENV OTEL_SERVICE_NAME=order-service
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse:3000/api/public/otel
ENV OTEL_TRACES_SAMPLER=parentbased_traceidratio
ENV OTEL_TRACES_SAMPLER_ARG=1.0
ENV JAVA_TOOL_OPTIONS="-javaagent:/app/opentelemetry-javaagent.jar"
ENTRYPOINT ["java", "-jar", "app.jar"]
```

`JAVA_TOOL_OPTIONS` 是 JVM 自动读取的环境变量，agent 通过它挂载——这样 `docker run` 不用改任何启动命令。

### 19. Python 与 Java 接入对比

| 维度 | Python | Java |
|------|--------|------|
| 自动 instrumentation | 每个库装一个 instrumentation 包，代码里调一行 | 一个 javaagent jar，零代码 |
| 修改业务代码 | 需要（`FastAPIInstrumentor.instrument_app(app)` 等） | 不需要 |
| 当前 Span 记录方式 | `with start_as_current_span(...)`（自动 end） | `span.makeCurrent()` + finally `span.end()` |
| 日志 trace_id 注入 | contextvars + structlog processor（本项目）或 logging instrumentation | MDC + Logback pattern（agent 自动注入） |
| 全局配置方式 | `trace.set_tracer_provider(provider)` | `GlobalOpenTelemetry.set(...)` 或 agent 自动 |
| 配置途径 | 代码或 `OTEL_*` 环境变量 | `-D` 系统属性或 `OTEL_*` 环境变量 |
| 语言成熟度 | API/SDK 稳定，Logs 信号较弱 | 全栈最成熟（agent 覆盖几百个库） |

一句话：**Python 接入是「装包 + 改几行代码」，Java 接入是「下个 jar + 加个启动参数」**。殊途同归，最后都发 OTLP 给同一个后端（比如本项目的 Langfuse）。

---

## 第四部分　Python AI Agent（本项目）Tracing 与 Log 全解析

这一部分把前面所有概念落到本项目的真实代码上。读完你应该能：看懂每一处埋点为什么这么写、自己改造成新项目、调优和排错。

### 20. 本项目的可观测性架构与数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│  FastAPI 进程 (space-aiagent)                                        │
│                                                                     │
│  main.py lifespan                                                    │
│   ├─ setup_logging()        structlog + trace_id 注入 processor      │
│   └─ setup_telemetry()                                              │
│        ├─ TracerProvider(Resource=space-aiagent, Sampler=ratio)      │
│        ├─ Langfuse(public_key, secret_key, base_url)                 │
│        │     └─ 自动挂 LangfuseSpanProcessor（Batch 语义）            │
│        └─ FastAPIInstrumentor.instrument_app(app)                    │
│                                                                     │
│  业务埋点（手动 Span）                                                │
│   ws.session ── orchestrator.llm                                     │
│              ├─ orchestrator.task ── subagent.llm                    │
│              │                      ├─ tool.createScenario           │
│              │                      └─ tool.addPointEntity           │
│              └─ orchestrator.tool.<name>                             │
│                                                                     │
│  每条 structlog 日志                                                  │
│   └─ add_trace_info processor 注入 trace_id / span_id                │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  │ OTLP HTTP POST（批量、异步）
                                  │ /api/public/otel
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  自托管 Langfuse v3 (docker compose, 6 服务)                          │
│  langfuse-web:3000  ← UI + OTLP 接收                                 │
│  langfuse-worker    ← 异步写入                                        │
│  clickhouse         ← trace 存储                                     │
│  postgres           ← 元数据                                         │
│  redis              ← 队列                                           │
│  minio              ← S3 兼容对象存储（events/media）                 │
└─────────────────────────────────────────────────────────────────────┘
```

**数据流文字版**：

1. FastAPI 启动 → `lifespan` 调 `setup_telemetry(settings)` → 装配 TracerProvider + Langfuse SDK
2. WebSocket 请求进来 → `run_agent` 用 `optional_span("ws.session")` 起 root Span
3. Agent 执行过程中，各 middleware 用 `optional_span("orchestrator.llm")` 等起子 Span，挂属性（延迟、返回码、工具名）
4. 同时，structlog 的 `add_trace_info` processor 给**每条业务日志**注入当前 `trace_id`
5. Span 结束 → LangfuseSpanProcessor 攒批 → 异步 OTLP POST 给 `langfuse-web:3000/api/public/otel`
6. Langfuse 把 trace 写入 ClickHouse → UI 展示 trace 树 + token 归因

### 21. 自托管 Langfuse v3（Docker Compose 详解）

本项目的 `docker/observability/docker-compose.yml` 起了 6 个服务，下面逐个解释为什么需要它。

```yaml
services:
  langfuse-web:      # Langfuse 主服务：UI + API + OTLP 接收端点（端口 3000）
  langfuse-worker:   # 后台 worker：异步把 events 从 Redis 写入 ClickHouse/S3
  clickhouse:        # 列式数据库：存所有 trace/event 数据（查询快）
  postgres:          # 关系数据库：存用户/项目/API key/标注等元数据
  redis:             # 队列：web 收到数据 → 入队 → worker 消费
  minio:             # S3 兼容对象存储：存大块 events/blobs（prompt 全文、媒体）
```

**为什么要 6 个而不是 1 个？** Langfuse v3 是生产级架构：web 只管接收和展示，重活（写入、聚合）丢给 worker 异步做，ClickHouse 专做海量 trace 列式查询，MinIO 存大块 payload，Redis 做缓冲。这套拆分保证高吞吐下 web 不卡。

**启动命令**（注意 `--env-file` 必须带）：

```bash
docker compose --env-file .env -f docker/observability/docker-compose.yml up -d
```

> ⚠️ **`--env-file .env` 为什么必须带**：`.env` 在仓库根目录，而 compose 用 `-f` 指定 compose 文件后，**默认从 compose 文件所在目录**（`docker/observability/`）找 `.env`。不加的话读不到 `LANGFUSE_SALT` / `LANGFUSE_ENCRYPTION_KEY` / `LANGFUSE_NEXTAUTH_SECRET`，Langfuse 启动会失败。

**凭证生成**（首次部署前在 `.env` 里填好）：

```bash
# NEXTAUTH_SECRET / SALT（任意强随机串）
openssl rand -base64 32

# ENCRYPTION_KEY（必须 hex 64 字符，base64 会被 Langfuse 拒绝）
openssl rand -hex 32

# 项目 ID（hex 32 字符，可选）
openssl rand -hex 16
```

`.env.example` 已经列好了所有要填的变量：

```bash
# Langfuse docker-compose 所需
LANGFUSE_NEXTAUTH_SECRET=replace-with-openssl-rand-base64-32
LANGFUSE_SALT=replace-with-openssl-rand-base64-32
LANGFUSE_ENCRYPTION_KEY=replace-with-openssl-rand-hex-32

# 首次启动自动初始化的项目信息（免 UI 注册）
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-space-aiagent-dev
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-space-aiagent-dev
LANGFUSE_INIT_USER_EMAIL=admin@space-aiagent.local
LANGFUSE_INIT_USER_PASSWORD=SpaceAIAgent2026!
```

**端口暴露策略**（来自 compose 注释）：

- `langfuse-web:3000` → 对外暴露（你要访问 UI 和发 OTLP）
- `minio:9090` → 对外暴露（看对象存储内容，可选）
- 其他（clickhouse/postgres/redis）→ 绑 `127.0.0.1`，仅本机访问（安全）

**首次启动后的接线**：

1. `docker compose up -d` → 首次启动用 `LANGFUSE_INIT_*` 自动创建 org/project/user，**省去 UI 注册步骤**
2. 浏览器开 `http://localhost:3000`，用 `admin@space-aiagent.local` / `SpaceAIAgent2026!` 登录
3. 项目里会有一对 API key，对应 `.env` 里的 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`
4. 把这两个 key 填到 `.env` 的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
5. 启动 FastAPI，发请求，去 Langfuse UI 看 trace

### 22. 代码逐文件剖析

#### 22.1 `infrastructure/observability/tracing.py` —— 装配中枢

这是整个可观测性的心脏。核心函数 `setup_telemetry`：

```python
def setup_telemetry(settings: Settings) -> None:
    global _initialized
    if _initialized:
        logger.warning("observability.already_initialized")
        return

    cfg: ObservabilityConfig = settings.observability
    if not cfg.enabled:                          # ★ 总开关：false 直接 return，全局走 NoOp
        logger.info("observability.disabled")
        return

    # base_url 去掉 /api/public/otel 后缀，Langfuse SDK 内部会拼
    base_url = cfg.langfuse_endpoint.rsplit("/api/public/otel", 1)[0]

    # ①② Resource + Sampler + TracerProvider（标准 OTel 五步法的前两步）
    provider = TracerProvider(
        resource=Resource.create({
            "service.name": cfg.service_name,
            "service.version": cfg.service_version,
        }),
        sampler=sampling.TraceIdRatioBased(cfg.sampler_ratio),
    )
    trace.set_tracer_provider(provider)          # ④ 设全局

    # ③ Exporter + SpanProcessor：Langfuse SDK 自动挂
    #    延迟 import：enabled=false 时根本不引入 langfuse 依赖
    from langfuse import Langfuse
    Langfuse(
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
        base_url=base_url,
        sample_rate=cfg.sampler_ratio,
    )

    _initialized = True
```

**几个值得品味的设计**：

1. **`_initialized` 幂等守卫**：防止 lifespan 被多次触发导致重复装配。
2. **`enabled=false` 早返回**：这是「对业务零依赖」的关键。OTel SDK 默认的 `trace.get_tracer_provider()` 返回的是 `ProxyTracer`（NoOp），所有 `start_as_current_span` 都是空操作。业务代码完全无感、零开销。
3. **`base_url` 容错**：配置里写的是完整 OTLP endpoint（`http://localhost:3000/api/public/otel`），但 Langfuse Python SDK 的 `base_url` 参数要的是**根地址**（`http://localhost:3000`），所以用 `rsplit` 去掉后缀。这样配置项可以和「通用 OTLP endpoint」概念统一。
4. **延迟 import langfuse**：放在 `enabled` 判断之后。`enabled=false` 时连 langfuse 包都不加载，真正做到零依赖。
5. **Langfuse SDK 自动挂 SpanProcessor**：你看不到 `add_span_processor` 的调用——Langfuse 的 `Langfuse()` 构造函数会自动把 `LangfuseSpanProcessor` 挂到**全局 TracerProvider**。这是 Langfuse SDK 的便利设计，也是为什么本项目不用手写 Exporter。

`optional_span` —— 业务埋点的便利封装：

```python
@contextmanager
def optional_span(name: str, **attributes: Any) -> Iterator[Span]:
    """用法：
        with optional_span("orchestrator.llm", thread_id=tid) as span:
            result = handler(request)
            span.set_attribute("response.code", code)
    """
    tracer = get_tracer("space_aiagent")
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        yield span
```

它把「取 tracer + 起 span + 批量设属性」打包成一个 context manager。`enabled=false` 时 tracer 是 NoOp，`start_as_current_span` 返回无效 span，`set_attribute` 是空操作——所以叫 **optional**（可选 span）。业务代码用 `with optional_span(...)` 包一层，零成本拿到可观测性。

`shutdown_telemetry` —— 优雅关闭：

```python
def shutdown_telemetry() -> None:
    global _initialized
    if not _initialized:
        return
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()        # ★ flush 批队列，避免 trace 丢失
    _initialized = False
```

在 `main.py` 的 `lifespan` finally 里调用，保证进程退出前把内存里的 Span 全部发出去。

#### 22.2 `infrastructure/observability/processors.py` —— 日志关联

整个文件就 22 行，但它是 **Trace ↔ Log 关联的唯一接合点**：

```python
from opentelemetry import trace

def add_trace_info(_, __, event_dict):
    """structlog processor：注入 trace_id/span_id（如果当前有 active span）"""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:                              # ★ 无 active span 跳过，零开销
        event_dict["trace_id"] = f"{ctx.trace_id:032x}"
        event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict
```

**`ctx.is_valid` 这个判断是零开销保障**：`enabled=false` 或当前没在 span 上下文里时，OTel 返回的是 `INVALID_SPAN_CONTEXT`，`is_valid=False`，直接 return，不影响日志性能。

**格式说明**：
- `trace_id` 是 128 bit，格式化成 32 位十六进制（`:032x` 表示至少 32 位、不足补零）
- `span_id` 是 64 bit，格式化成 16 位十六进制（`:016x`）

这两个格式和 W3C TraceContext、Langfuse UI 里显示的 ID 格式**完全一致**，所以你能直接复制 Langfuse 里的 trace_id 粘贴到 Loki 里搜。

#### 22.3 `infrastructure/logging.py` —— processor 链挂载点

`setup_logging` 里把 `_add_trace_info` 放进 structlog 的 `shared_processors`：

```python
# 延迟 import 避免 logging ↔ observability 循环导入
from space_aiagent.infrastructure.observability.processors import (
    add_trace_info as _add_trace_info,
)

shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    _add_thread_name,
    _add_caller_info,
    _add_trace_info,                 # ← 这里：每条日志注入 trace_id/span_id
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]
```

注意**延迟 import 的注释**——`logging.py` 和 `observability/processors.py` 互相依赖（observability 的 tracing.py 里 `get_logger` 来自 logging），所以 processors 的 import 放在函数内部，避免模块加载期循环依赖。这种细节是工程化的体现。

无论日志最终走控制台（Spring 风格）还是文件（JSON），都经过 `shared_processors`，所以两种输出都带 `trace_id`。

#### 22.4 `infrastructure/config.py` —— ObservabilityConfig

```python
class ObservabilityConfig(BaseSettings):
    enabled: bool = False                                    # 默认关，安全
    service_name: str = "space-aiagent"
    service_version: str = "0.1.0"
    langfuse_endpoint: str = "http://localhost:3000/api/public/otel"
    langfuse_public_key: str = ""                            # 从 .env 注入
    langfuse_secret_key: str = ""
    sampler_ratio: float = 1.0
```

在 `_apply_yaml_to_settings` 里：

```python
flat["observability"] = ObservabilityConfig(
    enabled=obs_cfg.get("enabled", False),
    service_name=obs_cfg.get("service_name", app_cfg.get("name", "space-aiagent")),
    service_version=obs_cfg.get("service_version", app_cfg.get("version", "0.1.0")),
    langfuse_endpoint=obs_cfg.get("langfuse_endpoint", "..."),
    langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),  # 凭据走 .env
    langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
    sampler_ratio=float(obs_cfg.get("sampler_ratio", 1.0)),
)
```

**配置分离原则的体现**：运行参数（endpoint、采样率、service 名）走 YAML，**敏感凭证（key）走 `.env`**，不进 Git。`application.yaml` 里 `enabled: true`，但 `.env` 里 key 留空时，Langfuse SDK 会优雅降级（warning 但不崩溃），业务照常跑。

#### 22.5 `main.py` —— 生命周期接线

```python
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_telemetry(settings)           # 启动期装配
    try:
        yield
    finally:
        shutdown_telemetry()            # 关闭期 flush


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(...)                  # 日志要先于 telemetry（telemetry 内部用 logger）
    app = FastAPI(..., lifespan=lifespan)
    app.add_middleware(CORSMiddleware, ...)

    # 自动 instrumentation：仅当 observability.enabled=true
    if settings.observability.enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/health")

    return app
```

**`excluded_urls="/health"`**：健康检查路径不打 span，否则健康探针每秒一次的请求会污染 trace 数据。

**`setup_logging` 必须在 `setup_telemetry` 之前**：因为 `setup_telemetry` 内部调 `get_logger(__name__)` 记日志（"observability.disabled" / "observability.ready"），日志系统得先就绪。看 `create_app` 的顺序：先 `setup_logging` 再返回带 `lifespan` 的 app，`lifespan` 在 app 启动时才调 `setup_telemetry`，顺序正确。

### 23. 业务埋点：一棵真实的 Trace 树长什么样

以下是「用户说『添加文昌地面站』，但当前无场景」这个真实场景产生的 trace 树（简化）：

```
ws.session  (root, thread_id=t1, scene_name="")           ← api/websocket.py:run_agent
│  duration: 3.2s
│
├── orchestrator.llm                                      ← PrimaryAgentMiddleware.awrap_model_call
│   │  agent.thread_id=t1, llm.latency_ms=820, response.code=NO_SCENE
│   │
│   └── (LLM 调用：意图识别，返回 NO_SCENE)
│
└── orchestrator.task  (subagent.name=entity-agent)       ← PrimaryAgentMiddleware.awrap_tool_call
    │  tool.name=task, agent.thread_id=t1
    │
    └── subagent.llm                                      ← SubagentToolValidationMiddleware
        │  llm.latency_ms=650
        │
        └── tool.addPointEntity                           ← 子 Agent 工具调用前校验
            │  tool.success=false
            │  (被 ToolValidationMiddleware 短路：current_scene_name 为空 → NO_SCENE)
```

对应的代码埋点（`PrimaryAgentMiddleware`）：

```python
async def awrap_model_call(self, request, handler):
    start_ts = time.perf_counter()
    # ★ 业务埋点：orchestrator 的 LLM 调用
    with optional_span("orchestrator.llm", **{"agent.thread_id": self.thread_id}) as span:
        response = await handler(request)
        latency_ms = int((time.perf_counter() - start_ts) * 1000)
        span.set_attribute("llm.latency_ms", latency_ms)          # 挂延迟
        code = response_util.get_agent_response_code_from_model_response(response)
        if code:
            span.set_attribute("response.code", code)             # 挂业务返回码
    # ... 后续业务逻辑
```

```python
async def awrap_tool_call(self, request, handler):
    tool_name = request.tool_call.get("name", "?")
    span_name = "orchestrator.task" if tool_name == "task" else f"orchestrator.tool.{tool_name}"
    # ★ 业务埋点：工具调用（task 委派 / 普通工具）
    with optional_span(span_name, **{
        "agent.thread_id": self.thread_id,
        "tool.name": tool_name,
        **({"subagent.name": subagent_type} if subagent_type else {}),
    }) as span:
        try:
            result = await handler(request)
            span.set_attribute("tool.success", True)
            return result
        except Exception:
            span.set_attribute("tool.success", False)             # 异常也记录
            raise
        finally:
            span.set_attribute("tool.latency_ms", latency_ms)
```

**埋点命名的语义层次**：注意 span 名字是有层次的——`orchestrator.llm` / `orchestrator.task` / `orchestrator.tool.<name>` / `subagent.llm` / `tool.<name>` / `ws.session`。这种命名让你在 Langfuse UI 的 trace 树里一眼看出「这是主控 Agent 的 LLM 调用」还是「子 Agent 的工具调用」，便于性能归因。

### 24. Trace 与 Log 的关联：trace_id 是那根线

一次请求里，同时产生了 trace（Langfuse 里）和 log（文件/Loki 里）。怎么关联？靠 `trace_id`。

业务代码里随手打的日志：

```python
logger.info("场景创建成功", scene_name="测试场景", thread_id="t1")
```

经过 `add_trace_info` processor 后，实际输出（JSON 格式）：

```json
{
  "timestamp": "2026-07-06T14:30:22.613",
  "level": "INFO",
  "event": "场景创建成功",
  "scene_name": "测试场景",
  "thread_id": "t1",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",   // ★ 同一条 trace
  "span_id": "00f067aa0ba902b7",
  "caller": "scene_management.py:42"
}
```

**关联工作流**：

1. 在 Langfuse UI 看到一条慢 trace（比如 `ws.session` 耗时 8s）
2. 复制它的 `trace_id`（32 位十六进制）
3. 去 Loki / ELK / `logs/space-aiagent.log` 搜 `trace_id=4bf92f3577b34da6a3ce929d0e0e4736`
4. 得到这条 trace 对应的所有日志，按时间排序，定位根因

反向也行：日志里看到一条 ERROR，拿它的 `trace_id` 去 Langfuse 看 trace 树，看到完整的调用链路和每步耗时。

这就是「**Logs and Traces Correlation**」——可观测性的核弹级能力，而本项目用 22 行 processor 就实现了。

### 25. enabled=false 时的零开销 NoOp 设计

这是本项目可观测性设计里最值得学的一点：**可观测性对业务零依赖**。

「零依赖」体现在三层：

**第一层：依赖零加载**
`setup_telemetry` 在 `enabled=false` 时**直接 return**，连 `from langfuse import Langfuse` 都不执行（延迟 import）。langfuse 包根本不会被加载进进程。

**第二层：Tracer 是 NoOp**
没调 `trace.set_tracer_provider(provider)` 时，OTel 的全局默认是 `ProxyTracer` 包着一个 `NoOpTracer`。`get_tracer(...)` 返回的 tracer，它的 `start_as_current_span(name)` 返回一个**无效 Span**（`SpanContext.is_valid == False`），`set_attribute` / `set_status` / `end` 全是空操作。

所以业务代码里的：

```python
with optional_span("orchestrator.llm") as span:
    ...
```

在 `enabled=false` 时，`optional_span` 内部 `start_as_current_span` 返回 NoOp span，整个 `with` 块除了几个函数调用开销，**几乎不增加任何成本**。

**第三层：日志 processor 零开销**
`add_trace_info` 里 `ctx.is_valid` 为 False 时立即 return，只是多一次 `get_current_span` + 一次条件判断。

**实际效果**：

| 场景 | CPU 开销 | 内存开销 | 网络开销 | 业务影响 |
|------|---------|---------|---------|---------|
| `enabled=false` | 几乎为零（几次函数调用） | 无 | 无 | 完全无感 |
| `enabled=true` Langfuse 宕机 | trace 在内存批队列堆积 | 批队列上限 | 重试失败 | 业务不中断（SDK 内部吞异常） |
| `enabled=true` 正常 | 每个 Span 微秒级 | 批队列 | OTLP POST | 可忽略 |

这意味着你可以**放心地把 observability 代码留在业务路径里**，开发/测试环境关掉，生产开掉，零负担。

### 26. 常见问题、调优与故障排查

**Q1：Langfuse UI 里看不到 trace？**
排查顺序：
1. `.env` 的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 是否填了？空的话 SDK 会 warning 但不上报
2. `application.yaml` 的 `observability.enabled` 是否 `true`？
3. `langfuse_endpoint` 是否可达？`curl http://localhost:3000/api/public/otel` 看响应
4. Span 是否真的被创建了？在 `optional_span` 内打条日志确认代码路径走到
5. 进程是否优雅退出？强 kill 会让批队列里的 Span 丢失——调 `shutdown_telemetry` 或减小刷盘间隔

**Q2：trace 有但子 Agent 的 span 掉链子（parent 关系丢失）？**
这是 LangGraph 的 context 隔离导致的（每个 node 在独立 `copy_context()` + `asyncio.create_task` 里跑）。LangGraph 通常会在 node 边界正确接力 trace context，但如果你自定义了 task 调度，需要手动 `contextvars.copy_context()` 把当前 context 带过去。本项目目前未遇到，因为 middleware 的 span 都在同一个 await 链里。

**Q3：采样率怎么设？**
- dev / staging：`1.0`（全量），方便排查
- prod 高流量：`0.1` 甚至 `0.01`，配合 `ParentBased` 保证一个 trace 要么全要要么全不要（不要只采一半，会让 trace 树断裂）
- 本项目用 `TraceIdRatioBased`，生产建议包一层 `ParentBased`

**Q4：批处理参数怎么调？**
Langfuse SDK 默认值一般够用。需要调时用环境变量（见 `application.yaml` 注释）：

```bash
export LANGFUSE_FLUSH_AT=50          # 攒满 50 条就 flush
export LANGFUSE_FLUSH_INTERVAL=1000  # 或 1 秒就 flush（毫秒）
```

调大 → 网络请求少、吞吐高，但进程崩溃丢的多；调小 → 实时性好、丢的少，但网络请求多。

**Q5：Langfuse 宕机了，业务会挂吗？**
不会。Langfuse Python SDK 内部对所有上报操作做了 try/except，网络异常只会记 warning，不会抛给业务。再加上 Span 上报是后台线程异步的，业务线程完全不感知。**这就是「可观测性对业务零依赖」的兜底**。

**Q6：trace 数据太大，ClickHouse 磁盘要爆？**
- 降采样率（`sampler_ratio`）
- 定期清理老 trace（Langfuse UI 有 retention 设置）
- 高基数属性（如 `user.id`、`thread_id`）谨慎设为 attribute，会膨胀索引

### 27. 从零复现的 Step-by-step 清单

把这套方案搬到新项目，照这个清单走：

**Step 1：起 Langfuse 后端**
```bash
# 在仓库根目录准备 .env（参照 .env.example 填好 SALT/ENCRYPTION_KEY/NEXTAUTH_SECRET）
docker compose --env-file .env -f docker/observability/docker-compose.yml up -d
# 浏览器开 http://localhost:3000 登录，确认项目创建成功
```

**Step 2：装依赖**
```bash
pip install opentelemetry-api opentelemetry-sdk \
            opentelemetry-instrumentation-fastapi \
            "langfuse>=3.0,<4.0"
```

**Step 3：写 tracing 装配**（直接抄 `tracing.py`）
- 实现 `setup_telemetry(settings)` / `shutdown_telemetry()` / `get_tracer()` / `optional_span()`

**Step 4：写日志 processor**（直接抄 `processors.py`）
- 实现 `add_trace_info`，挂进你的 structlog/logging processor 链

**Step 5：配置**（抄 `application.yaml` + `.env`）
```yaml
observability:
  enabled: true
  service_name: my-service
  langfuse_endpoint: http://localhost:3000/api/public/otel
  sampler_ratio: 1.0
```
```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

**Step 6：应用生命周期接线**（抄 `main.py`）
```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_telemetry(get_settings())
    try:
        yield
    finally:
        shutdown_telemetry()

app = FastAPI(lifespan=lifespan)
if settings.observability.enabled:
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health")
```

**Step 7：业务埋点**
```python
from mypkg.observability import optional_span

with optional_span("my.operation", user_id=uid) as span:
    result = do_work()
    span.set_attribute("result.size", len(result))
```

**Step 8：验证**
- 启动应用，发一个请求
- 去 Langfuse UI `http://localhost:3000` → Traces，看到 span 树
- 复制 trace_id，在日志里搜，确认日志带上了同一个 trace_id

**Step 9：生产化**
- 采样率降到合理值
- 配置 `LANGFUSE_FLUSH_AT` / `LANGFUSE_FLUSH_INTERVAL`
- 加 Prometheus + Grafana（系统指标，Phase 1A-2 的下一步）
- 加 Loki/ELK（日志聚合，串联 trace_id）

---

## 附录：关键概念速查表

| 术语 | 一句话解释 |
|------|-----------|
| **Observability** | 从外部行为推导内部状态的能力，处理「未知未知」 |
| **Trace** | 一次请求的完整执行路径，一棵 Span 树 |
| **Span** | 一段工作，有起止时间、属性、状态、父指针 |
| **trace_id / span_id** | 128bit / 64bit 的唯一标识，关联 Trace/Log/Span 的钥匙 |
| **Resource** | Span 的「出身」，最关键是 `service.name` |
| **Sampler** | 决定 Span 是否记录，控成本 |
| **SpanProcessor** | Span 结束后处理（批量/同步） |
| **Exporter** | 把 Span 发后端，发 OTLP |
| **OTLP** | OTel 数据传输协议，HTTP/gRPC |
| **Collector** | 可选中转站，receiver→processor→exporter |
| **Context Propagation** | trace_id 跨进程传递，W3C TraceContext |
| **Langfuse** | AI 维度的 OTLP Traces 后端，自托管 |
| **Prometheus** | Metrics 后端，pull 模型，和 Traces 无关 |
| **Grafana** | 可视化层，连数据源，不存数据 |
| **MDC / contextvars** | Java / Python 的线程/协程上下文，日志注入 trace_id 用 |
| **Semantic Conventions** | 官方属性命名约定（`gen_ai.*`、`http.*`、`db.*`） |

---

> **结语**：OpenTelemetry 的价值不在于它本身是个多强的工具，而在于它是一个**全行业共识的标准**——让 instrumentation 一次编写、后端随意切换、跨语言跨服务拼成完整链路。本项目的实践（OTel SDK + 自托管 Langfuse + structlog trace_id 注入 + 零开销 NoOp 兜底）是一个可直接复用的生产级模板。掌握了这套，无论你下一个项目是 Python AI Agent、Java 微服务、还是混合栈，都能用同一套心智模型落地可观测性。
>
> 进一步的系统指标（Prometheus）和可视化（Grafana）规划，见 [CLAUDE.md](../CLAUDE.md) 的 Phase 1A-2 / 1A-3，以及 [`python教程.md` §26](./python教程.md)。
