import yaml
import logging

from langchain_core.language_models import BaseChatModel
from space_aiagent.infrastructure.config import CONFIG_DIR
from space_aiagent.models.schemas import SubagentClassification

logger = logging.getLogger(__name__)
_SUBAGENTS_CONFIG = CONFIG_DIR / "subagents.yaml"

def load_subagents_yaml_config():

    config_text = _SUBAGENTS_CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)
    return config

async def resolve_subagent_type(
    intent: str,
    captured_subagent: str | None,
    model: BaseChatModel,
) -> str:
    """
    解析自动续接的 subagent_type
    """
    if captured_subagent:
        return captured_subagent

    subagents = load_subagents_yaml_config()["agents"]
    agents_desc: str = "\n".join(
        f"- {s['name']}: {s['description']}" for s in subagents
    )

    valid_names: set[str] = {s["name"] for s in subagents}
    prompt: str = (
        "你是航天分析平台的路由分类器。根据用户意图选择最合适的子 Agent，"
        "以 JSON 格式输出结果。\n\n"
        f"可用子 Agent:\n{agents_desc}\n\n"
        f"用户意图: {intent}\n\n"
        "请输出 json: {\"subagent_type\": \"<子 agent name>\"}。"
    )

    classifier = model.with_structured_output(SubagentClassification)
    try:
        result = await classifier.ainvoke(prompt)
    except Exception:
        logger.exception("LLM 路由分类失败，回退 %s", subagents[0]["name"])
        return subagents[0]["name"]

    if not result or not result.subagent_type or result.subagent_type not in valid_names:
        logger.warning(
            "LLM 分类返回无效只智能体: %s, 有效的子智能体名有: %s，回退 %s",
            result.subagent_type, valid_names, subagents[0]["name"],
        )
        return subagents[0]["name"]
    logger.info("LLM 路由分类: intent=%s → %s", intent, result.subagent_type)
    return result.subagent_type