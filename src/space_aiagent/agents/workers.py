"""从声明式配置构建 V2 DeepAgents Worker。"""

from pathlib import Path

import yaml
from deepagents.backends.protocol import BackendProtocol

from space_aiagent.infrastructure.backend import build_agent_backend
from space_aiagent.infrastructure.config import CONFIG_DIR, get_settings
from space_aiagent.infrastructure.llm import build_flash_model, build_model
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.skill.catalog import SkillCatalog
from space_aiagent.middleware import RetryMiddleware, SkillRoutingMiddleware, WorkerToolValidationMiddleware
from space_aiagent.tools.registry import get_tools

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_SKILL_USAGE_PROMPT = (_PROMPTS_DIR / "skill_usage.md").read_text(encoding="utf-8")
_WORKERS_CONFIG = CONFIG_DIR / "workers.yaml"


def _load_worker_config() -> dict:
    return yaml.safe_load(_WORKERS_CONFIG.read_text(encoding="utf-8"))


def load_workers(backend: BackendProtocol | None = None) -> list[dict]:
    """加载 Worker 模型、工具、Skill、中间件及 HITL 配置。"""
    config = _load_worker_config()
    backend = backend or build_agent_backend()
    model = build_model()
    flash_model = build_flash_model()
    retry_config = get_settings().retry
    workers: list[dict] = []

    for worker_config in config["workers"]:
        tools = get_tools(worker_config["tools"])
        prompt = (_PROMPTS_DIR / worker_config["prompt_file"]).read_text(encoding="utf-8")
        skills_paths = list(worker_config.get("skills", []))
        if skills_paths:
            prompt = f"{prompt.rstrip()}\n\n{_SKILL_USAGE_PROMPT}"
        business_tool_names = {tool.name for tool in tools}
        catalog = SkillCatalog.from_backend(backend, skills_paths, business_tool_names)

        middleware = []
        if skills_paths:
            middleware.append(
                SkillRoutingMiddleware(
                    agent_name=worker_config["name"],
                    catalog=catalog,
                    business_tool_names=business_tool_names,
                    router_model=flash_model,
                    retry_config=retry_config,
                )
            )
        middleware.extend(
            [
                WorkerToolValidationMiddleware(agent_name=worker_config["name"]),
                RetryMiddleware(retry_config),
            ]
        )

        worker: dict = {
            "name": worker_config["name"],
            "description": worker_config["description"],
            "model": model,
            "tools": tools,
            "system_prompt": prompt,
            "middleware": middleware,
        }
        if interrupt_on := worker_config.get("interrupt_on"):
            worker["interrupt_on"] = interrupt_on
        if skills_paths:
            worker["skills"] = skills_paths
        workers.append(worker)

    return workers
