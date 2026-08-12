"""Agent 共享 Backend 装配。"""

from deepagents.backends import FilesystemBackend
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol

from space_aiagent.infrastructure.config import PROJECT_ROOT

_KNOWLEDGE_DIR = PROJECT_ROOT / "config" / "knowledge"
_SKILLS_DIR = PROJECT_ROOT / "src" / "space_aiagent" / "skills"


def build_agent_backend() -> BackendProtocol:
    """构造供 Worker 与 Skill 路由器共同使用的 Backend。"""
    return CompositeBackend(
        default=FilesystemBackend(root_dir=str(_KNOWLEDGE_DIR), virtual_mode=True),
        routes={"/skills/": FilesystemBackend(root_dir=str(_SKILLS_DIR), virtual_mode=True)},
    )
