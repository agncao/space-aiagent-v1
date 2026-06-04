"""
Agent 层

多 Agent 架构:
- Orchestrator: 主控 Agent，接收用户输入，规划任务，分发到子 Agent
- SceneAgent: 场景管理子 Agent，处理场景相关操作
- EntityAgent: 实体管理子 Agent，处理实体和轨道相关操作

使用 DeepAgent (deepagents) 作为 Agent Harness。
"""
