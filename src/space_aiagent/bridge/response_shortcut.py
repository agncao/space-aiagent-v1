"""确定性 case 的预构建响应数据

已知确定性 case（如"无场景上下文"）预构建 AgentResponse，作为 ToolMessage
payload 的结构化数据源。中间件把它转成 ToolMessage 内容，配合 Command(goto=END)
终止子 Agent 图——状态由 LangGraph 自动持久化到 checkpointer，多轮对话能
正确恢复上下文。

复用 response_renderer.DEFAULT_TEMPLATES 作为 summary，避免双份维护。
"""

from space_aiagent.bridge.response_renderer import DEFAULT_TEMPLATES
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse

# shortcut key → 预构建 AgentResponse
# 新增确定性 case 只需在此追加一条
_SHORTCUT_RESPONSES: dict[str, AgentResponse] = {
    "no_scene": AgentResponse(
        status="info",
        code="NO_SCENE",
        summary=DEFAULT_TEMPLATES["NO_SCENE"],
        suggestions=["创建场景", "打开已有场景"],
    ),
    "task_loop_guard": AgentResponse(
        status="confirm",
        code="TASK_LOOP_GUARD",
        summary=DEFAULT_TEMPLATES["TASK_LOOP_GUARD"],
        suggestions=["补充要操作的对象", "明确说明要修改的属性"],
    ),
}
