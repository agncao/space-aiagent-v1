"""AgentResponse 渲染稳定化中间件（已退役）

历史职责：模板渲染稳定化 AIMessage.content，由 ResponseRenderer 完成。
模板渲染废弃后（响应直接用 AgentResponse.summary + suggestions 出口渲染，
见 response_util.render），本中间件已退役，类保留挂在 orchestrator / 子 Agent
中间件链上作为占位，便于未来在此挂载新的稳定化逻辑。退役模式参考 LoggingMiddleware。
"""

from langchain.agents.middleware.types import AgentMiddleware, AgentState

from space_aiagent.infrastructure.logging import get_logger

logger = get_logger(__name__)


class ResponseStabilizationMiddleware(AgentMiddleware):
    """AgentResponse 稳定化中间件（已退役，占位保留）"""

    state_schema = AgentState

    def __init__(self, agent_name: str = "unknown") -> None:
        super().__init__()
        self.agent_name = agent_name
