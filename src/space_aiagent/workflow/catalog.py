"""Graph Planner 可见的 Worker 目录。"""

from pathlib import Path

import yaml
from pydantic import BaseModel

from space_aiagent.infrastructure.config import CONFIG_DIR


class WorkerDefinition(BaseModel):
    """Graph 可见的最小 Worker 能力描述。"""

    name: str
    description: str


class WorkerCatalog:
    """向 Planner 只暴露 Worker 描述，并内部维护自动发现的事实提供者。"""

    def __init__(
        self,
        workers: list[WorkerDefinition],
        fact_providers: dict[str, set[str]] | None = None,
    ) -> None:
        self._workers = {worker.name: worker for worker in workers}
        if len(self._workers) != len(workers):
            raise ValueError("WorkerCatalog 中存在重复 Worker")
        self._fact_providers = {fact: frozenset(providers) for fact, providers in (fact_providers or {}).items()}

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "WorkerCatalog":
        catalog_path = path or CONFIG_DIR / "workers.yaml"
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
        raw_workers = payload.get("workers", [])
        if not isinstance(raw_workers, list) or not raw_workers:
            raise ValueError(f"WorkerCatalog 为空: {catalog_path}")
        from space_aiagent.tools.contracts import get_workflow_tool_contract
        from space_aiagent.tools.registry import get_tools

        fact_providers: dict[str, set[str]] = {}
        for item in raw_workers:
            for tool in get_tools(item.get("tools", [])):
                for effect in get_workflow_tool_contract(tool).effects:
                    fact_providers.setdefault(effect, set()).add(item["name"])
        workers = [WorkerDefinition(name=item["name"], description=item["description"]) for item in raw_workers]
        return cls(workers, fact_providers)

    def contains(self, name: str) -> bool:
        return name in self._workers

    def providers_for(self, fact: str, *, exclude: set[str] | None = None) -> set[str]:
        return set(self._fact_providers.get(fact, set())) - (exclude or set())

    def planner_context(
        self,
        *,
        exclude: set[str] | None = None,
        include: set[str] | None = None,
    ) -> str:
        excluded = exclude or set()
        return "\n".join(
            f"- {worker.name}: {worker.description}"
            for worker in self._workers.values()
            if worker.name not in excluded and (include is None or worker.name in include)
        )
