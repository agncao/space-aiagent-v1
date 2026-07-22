from typing import Any
from urllib.parse import quote, urlsplit

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse, ResponseCode
from space_aiagent.models.schemas import ScenarioInfo

logger = get_logger(__name__)


def find_agent_response_tool_call(response: ModelResponse) -> dict[str, Any] | None:
    """在 ModelResponse 中查找名为 AgentResponse 的 tool_call，找不到返回 None"""
    for msg in response.result:
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.get("name") != "AgentResponse":
                continue
            return tc
    return None


def parse_code_by_model_response(response: ModelResponse) -> str | None:
    """
    从 ModelResponse 中提取 AgentResponse tool_call 的 code 字段
    """
    agent_tc = find_agent_response_tool_call(response)
    if not agent_tc:
        return None
    return agent_tc.get("args", {}).get("code")


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _scenario_link(scenario: ScenarioInfo) -> str:
    label = _escape_table_cell(scenario.scene_name).replace("[", "\\[").replace("]", "\\]")
    file_url = scenario.file_url.strip()
    parsed = urlsplit(file_url)
    if not file_url or parsed.scheme not in {"", "http", "https"} or (not parsed.scheme and parsed.netloc):
        return label
    safe_url = quote(file_url, safe="/:@?&=%+,-._~")
    return f"[{label}]({safe_url})"


def _render_scenario_table(scenario_infos: list[dict[str, Any] | ScenarioInfo]) -> str:
    scenarios: list[ScenarioInfo] = []
    for item in scenario_infos:
        try:
            scenarios.append(item if isinstance(item, ScenarioInfo) else ScenarioInfo.model_validate(item))
        except (TypeError, ValueError):
            logger.warning("忽略无效场景查询结果", item=item)

    if not scenarios:
        return "未查询到符合条件的场景。"

    rows = [
        f"| {_scenario_link(item)} | {_escape_table_cell(item.update_time or '-')} | "
        f"{_escape_table_cell(item.uploader_name or '-')} |"
        for item in scenarios
    ]
    return "\n".join(
        [
            f"查询成功，共找到 {len(scenarios)} 个场景：",
            "",
            "| 场景名 | 更新时间 | 上传人 |",
            "| --- | --- | --- |",
            *rows,
        ]
    )


def render(
    response: AgentResponse,
    scenario_infos: list[dict[str, Any] | ScenarioInfo] | None = None,
) -> str:
    """将结构化响应渲染为自然语言"""

    if scenario_infos is None and response.code == ResponseCode.SCENE_QUERIED:
        scenario_infos = []

    # 查询数据来自工具写入的 state，不让 LLM 负责复制列表。即使模型误判 code，
    # 只要本轮确实执行了场景查询，也优先输出完整、确定性的表格。
    if scenario_infos is not None:
        if response.code != ResponseCode.SCENE_QUERIED:
            logger.warning("场景查询响应码不一致，按查询结果渲染", response_code=response.code)
        return _render_scenario_table(scenario_infos)

    def _fallback_text() -> str:
        parts = [response.summary]
        if response.suggestions:
            parts.append(" **接下来您可以：**\n")
            parts.append("\n".join(f"- {s}" for s in response.suggestions))
        return "\n\n".join(parts)

    return _fallback_text()
