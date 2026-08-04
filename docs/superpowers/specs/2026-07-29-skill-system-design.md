# SP2 — Skill 系统设计（三层架构：Package / Backend / Policy）

- 日期：2026-07-29（v2 重写，取代 v1 的"最小可加载"设计）
- 状态：设计稿（待 review）
- 范围：可分发、跨平台、带安全约束的产品级 Skill 系统。本轮 = Phase-1（local backend）；Docker = Phase-2（下一阶段）。
- 依赖：SP1（HITL 传输层）✅ 已落地（commit `3bce162`）。SP3（open_scenario 等业务 skill）依赖本 spec。
- 参考：
  - 内置库：`/opt/miniconda3/envs/space-aiagent-v1/lib/python3.13/site-packages/deepagents/`（0.6.12）
  - ERP_OPENCLAW：`~/projects/mashibing/HarnessEngineeringBased_DeepAgents_Course/ERP_OPENCLAW`（SkillsMiddleware + CompositeBackend + StoreBackend 用法）
  - LX_AICoding：`~/projects/mashibing/AICoding/LX_AICoding/agent/backends/local_shell.py`（Windows `BaseSandbox` 子类 + 命令拦截模板）

## 1. 背景与演进

v1 spec 只解决"让 skill 能加载"（内置 SkillsMiddleware + FilesystemBackend）。本 v2 按 **可分发到 805 所 Windows 环境**的产品要求升级为三层架构。HITL（SP1）已由用户独立完成，本 spec 不再涉及传输层。

**关键事实**（核实 0.6.12 源码）：deepagents 白送 **Package 层 + 管道**；**Policy 层 + OS/Docker backends 全靠自研**。

| 关注点 | 库 verdict | 证据 |
|---|---|---|
| Skill 加载（progressive disclosure） | **内置** | `middleware/skills.py` `SkillsMiddleware` |
| 复合后端（前缀路由） | **内置** | `backends/composite.py:118` `CompositeBackend` |
| StoreBackend（偏好/记忆） | **内置** | `backends/store.py:172`；ERP `main_agent.py:180-193` |
| Posix 本地 backend | **内置** | `backends/local_shell.py:27` `LocalShellBackend`（=FilesystemBackend+执行） |
| Windows 本地 backend | **自研** | 库无 OS-aware 执行；`utils.py:497` 拒绝 Windows 绝对路径 |
| Docker 沙箱 | **自研** | 仅 `sandbox.py:394` `BaseSandbox` ABC，实现 4 抽象方法 |
| 路径权限（读/写） | **内置**（route-scoped 限定） | `middleware/filesystem.py:84` `FilesystemPermission` |
| 命令白名单 | **自研** | `filesystem.py:745` 明确"execute 工具权限未实现" |
| 超时 | **内置** | backend `subprocess.run(timeout=)` + `filesystem.py:706` 上限 |
| 审计 | **自研** | 库无文件/exec 审计 |

**两个库硬约束（设计必须绕开）**：
1. `CompositeBackend.execute()` 永远走 default backend，**不能按路径路由**（`composite.py:539`）。→ 不可信命令关 Docker 只能靠 Policy 层，不是路由。
2. `permissions=` + 可执行 backend **抛 NotImplementedError**，除非所有 permission path 落在 CompositeBackend 路由前缀下（`filesystem.py:736`）。→ 路径限制必须 route-scoped。

## 2. 目标 / 非目标

**Phase-1 目标（本轮）**
1. orchestrator + 各子 Agent 按目录加载 skill（Package 层）。
2. OS 自适应本地 backend：开发/部署在 Windows 用 `WindowsLocalBackend`，posix 用 `PosixLocalBackend`。
3. Policy 层：路径限制（route-scoped）+ **命令真白名单**（解释器 + 自动发现 skill 脚本）+ 超时（内置）+ 基础审计。
4. 用户偏好经 StoreBackend 跨线程持久化。
5. `application.yaml` 配置式选 backend（`local`；`docker` 占位返"暂不支持"）。
6. 部署脚本 `install.sh`/`install.cmd`：测 OS、查 docker、问本地/docker；docker → 提示暂不支持；本地 → 装 runtime + 内置 skill 依赖。

**Phase-2（下一阶段，非本轮）**
- `DockerSandboxBackend`（子类 `BaseSandbox`）+ install 脚本 docker 分支 + CompositeBackend default 切 docker。

**非目标**
- 不写具体业务 skill（open_scenario 等 = SP3）。
- 不做 skill 市场 / 版本管理 / 多租户（Phase 5）。
- 不做 CPU/内存/cgroup 限制（超时是唯一资源约束，同 LX_AICoding）。

## 3. 架构总览

```
┌─ Package 层（内置 SkillsMiddleware）─────────────────────┐
│  project_root/skills/<scope>/<name>/{SKILL.md, scripts/, │
│                                     references/, assets/}│
│  <scope> = main | scene | entity | …（= 执行该 skill 的 Agent）│
└──────────────────────────────────────────────────────────┘
┌─ Backend 层（OS 相关）───────────────────────────────────┐
│  LocalBackend factory ── platform.system() ──┐           │
│   ├─ WindowsLocalBackend (移植 LX_AICoding) │           │
│   └─ PosixLocalBackend   (包 LocalShellBackend)         │
│  DockerSandboxBackend (Phase-2，子类 BaseSandbox)        │
└──────────────────────────────────────────────────────────┘
┌─ Policy 层（统一安全约束）───────────────────────────────┐
│  路径限制   : FilesystemPermission（route-scoped，内置） │
│  命令策略   : 真白名单（自研 CommandGuard，backend 内）  │
│  资源约束   : 仅超时（内置）                             │
│  基础审计   : structlog（自研，execute/file op）         │
└──────────────────────────────────────────────────────────┘
┌─ 管道（CompositeBackend，config 驱动 factory）───────────┐
│  default = LocalBackend（执行 + 兜底文件 op）            │
│  routes  = {"/skills/": local, "/knowledge/": local,     │
│             "/prefs/": StoreBackend}                     │
└──────────────────────────────────────────────────────────┘
┌─ 配置 / 部署 ────────────────────────────────────────────┐
│  application.yaml : skill.backend.mode / skill.policy.*  │
│  install.sh / install.cmd                                  │
└──────────────────────────────────────────────────────────┘
```

## 4. Package 层

### 4.1 目录布局（skills/ 挪到 project root）

```
<project_root>/skills/
├── main/                       # 挂 orchestrator（横切 skill）
│   └── skill-management/       # 参考 ERP_OPENCLAW skills/main/skill-management（skill 下载/分配生命周期）
│       └── SKILL.md
├── scene/                      # scene-agent scope
│   └── open_scene/SKILL.md     # SP3 落地
└── entity/                     # entity-agent scope
    └── orbit-report/
        ├── SKILL.md
        ├── scripts/generate_report.py
        └── references/report-format.md
```

> skills/ 从 v1 的 `src/space_aiagent/skills/` 挪到 project root，与 ERP_OPENCLAW 一致、便于分发打包、CompositeBackend default backend root = project root 直接覆盖。

### 4.2 Skill 包格式（Agent Skills 规范）

```
<name>/
├── SKILL.md          # 必需，YAML frontmatter + Markdown
├── scripts/          # 可选，可执行脚本（Policy 白名单自动收录）
├── references/       # 可选，参考文档（LLM 按需 read_file）
└── assets/           # 可选，静态资源
```

frontmatter（`skills.py:249-351` 解析）：`name`（=目录名，≤64，小写连字符）、`description`（≤1024）、可选 `license`/`compatibility`/`metadata`/`allowed-tools`（空格分隔）。

### 4.3 SkillsMiddleware 接入

- orchestrator：`create_deep_agent(..., skills=["/skills/main/"], backend=backend, ...)`（`graph.py:705,754` 自动挂）。
- 子 Agent：`config/subagents.yaml` 加 `skills: ["/skills/scene/"]`，`agents/subagents.py` 透传 `skills` 字段（`graph.py:628-630` 自动挂）。
- progressive disclosure：metadata 注入各 Agent system prompt；LLM 用内置 `read_file`（backend 存在时自动注入，`graph.py:260-263`）读 SKILL.md 全文。

## 5. Backend 层

### 5.1 LocalBackend factory（`infrastructure/backends/local.py` 新建）

```python
def build_local_backend(policy: PolicyConfig) -> BackendProtocol:
    if sys.platform == "win32":
        return WindowsLocalBackend(root_dir=PROJECT_ROOT, policy=policy)
    return PosixLocalBackend(root_dir=PROJECT_ROOT, policy=policy)
```

### 5.2 PosixLocalBackend（`infrastructure/backends/local_posix.py`）

子类内置 `LocalShellBackend`（`FilesystemBackend + SandboxBackendProtocol`），仅重写 `execute()` 注入 CommandGuard：

```python
class PosixLocalBackend(LocalShellBackend):
    def execute(self, command, *, timeout=None):
        denied = self.command_guard.check(command)        # 真白名单
        if denied:
            return ExecuteResponse(output=f"命令不在白名单：{denied}", exit_code=126)
        self.audit.log(command)                           # 审计
        return super().execute(command, timeout=timeout)  # 内置超时/subprocess
```

### 5.3 WindowsLocalBackend（`infrastructure/backends/local_windows.py`）

移植 LX_AICoding 的 Windows `BaseSandbox` 子类（`local_shell.py:91`）：实现 `execute`/`id`/`upload_files`/`download_files` 四抽象方法，Windows 子进程（`cmd.exe` via `subprocess(shell=True)`）+ CommandGuard + 超时。**实现期 spike**：先试 `WindowsLocalBackend(LocalShellBackend)`（文件 op 走 pathlib 跨平台，仅重写 execute）；若 LX_AICoding 选 `BaseSandbox` 是因 LocalShellBackend.execute 太 POSIX 化，则照搬其 `BaseSandbox` 路线。

### 5.4 DockerSandboxBackend（Phase-2，本轮不实现）

子类 `BaseSandbox`（`sandbox.py:394`），实现 4 抽象方法（docker run/exec 上传下载 + execute）。参考 `LangSmithSandbox`（`backends/langsmith.py:48`）。

## 6. Policy 层

### 6.1 命令真白名单（`infrastructure/backends/policy.py` 新建）— 核心自研

**设计目标**：严（非白名单一律拒）且可维护（加 skill 脚本不改配置）。白名单 = 三源合并：

1. **解释器**（config，默认 `["python", "python3"]`）：允许这些可执行文件作为命令首 token。
2. **skill 脚本自动收录**：agent 构建期扫 `skills/**/scripts/*` → 白名单自动含 `<解释器> <该脚本绝对路径>`。
3. **显式 allowlist**（config 可选）：额外整命令白名单（如 `pip install`，谨慎）。

判定（`CommandGuard.check(command) -> denied_reason | None`）：解析命令首 token + 参数；放行 iff（首 token ∈ 解释器 ∧ 其余参数是已收录的 skill 脚本路径）∨ 命令 ∈ 显式 allowlist；否则返回拒绝理由。注入在 backend `execute()` 内（subprocess 前）。

> 维护性：把 `generate_report.py` 丢进 `skills/entity/orbit-report/scripts/` → 自动被 `<python> <脚本>` 形式收录，无需改配置；LLM 试 `rm -rf`/`curl` → 拒。

### 6.2 路径限制（内置 FilesystemPermission，route-scoped）

```python
permissions=[
    FilesystemPermission(operations=["read","write"], paths=["/skills/**","/knowledge/**","/workspace/**"], mode="allow"),
    # 兜底 deny 由 CompositeBackend 路由范围天然限定（非路由路径不在可达范围）
]
```
因约束 2（§1），permission path 必须落在 CompositeBackend 路由前缀下 → `/skills/`、`/knowledge/` 必须是路由（见 §7）。

### 6.3 超时（内置）

- backend 层：`subprocess.run(timeout=)`（`local_shell.py:307`），超时 `exit_code=124`。
- middleware 上限：`max_execute_timeout`（`filesystem.py:706`，默认 3600）。config `skill.policy.timeout` 暴露。

### 6.4 审计（自研，structlog）

backend `execute()` 内：`logger.info("skill.exec", command=..., exit_code=..., duration=...)`，复用现有 structlog + `trace_id`/`span_id`。可选：高危 file op（write/edit）也记。

## 7. 管道：CompositeBackend factory（config 驱动）

`agents/orchestrator.py` 的 backend 改为 factory：

```python
def build_backend(settings) -> BackendProtocol:
    mode = settings.skill.backend.mode          # local | docker
    if mode == "docker":
        raise NotImplementedError("docker backend 暂不支持（Phase-2）")  # install 脚本已挡，双重保险
    local = build_local_backend(settings.skill.policy)
    return CompositeBackend(
        default=local,                          # execute + 兜底文件 op
        routes={
            "/skills/":    local,               # route-scoped（供路径权限 + 约束2）
            "/knowledge/": local,               # AGENTS.md
            "/prefs/":     StoreBackend(store=get_store(), namespace=lambda rt: ("prefs", user_id(rt))),
        },
    )
```

- `/skills/`、`/knowledge/` 路由到同一 local（为权限 scoping + 清晰边界）。
- `/prefs/` → StoreBackend（用户偏好，跨线程；namespace 按 user 隔离）。
- `memory=["/knowledge/AGENTS.md"]`（路径加 `/knowledge/` 前缀，走路由）。

## 8. 配置（`application.yaml`）

```yaml
skill:
  backend:
    mode: local            # local | docker（docker 占位）
  policy:
    timeout: 3600
    command_whitelist:
      interpreters: [python, python3]
      allowlist: []        # 显式额外整命令白名单
    path_permissions:
      - { operations: [read, write], paths: ["/skills/**", "/knowledge/**", "/workspace/**"], mode: allow }
```

新增 `SkillConfig`（`infrastructure/config.py`）：`backend.mode`、`policy.timeout`、`policy.command_whitelist.{interpreters,allowlist}`、`policy.path_permissions`。

## 9. 部署脚本（`install.sh` / `install.cmd`，project root）

两脚本同语义、平台各一份：
1. 测 OS（`uname` / `%OS%`）→ 报告。
2. 查 docker 可用性。
3. 问用户：本地 or docker。
4. docker → **提示"暂不支持，请选本地"**（Phase-1）。
5. 本地 → 检查/安装 python runtime + `pip install` 内置 skill 依赖（各 skill 的 `requirements.txt` 若有）。

## 10. 受影响文件

| 文件 | 改动 |
|---|---|
| `infrastructure/backends/`（新建） | `local.py`(factory)、`local_posix.py`、`local_windows.py`、`policy.py`(CommandGuard+audit) |
| `agents/orchestrator.py` | backend 改 `build_backend()` factory（CompositeBackend+LocalBackend+StoreBackend）；`skills=["/skills/main/"]`；`memory` 加前缀；`permissions=` |
| `agents/subagents.py` | 透传 `skills` 字段 |
| `config/subagents.yaml` | 各 Agent 加 `skills:` |
| `infrastructure/config.py` | 新增 `SkillConfig`（backend/policy） |
| `config/application.yaml` | 加 `skill:` 段 |
| `skills/`（project root，新建） | main/scene/entity scopes + skill 包 |
| `install.sh` / `install.cmd`（新建） | 部署脚本 |
| 测试 | 见 §11 |

## 11. 测试策略

- **CommandGuard 单元**：白名单三源（解释器 / 自动收录 skill 脚本 / 显式）各放行 case；`rm -rf`/`curl`/`format` 拒绝 case；Windows 危险命令（移植 LX_AICoding `_DANGEROUS_PATTERNS` 测试）。
- **backend 集成**（posix dev 机）：`PosixLocalBackend.execute("python skills/entity/orbit-report/scripts/generate_report.py")` 放行并执行；`execute("rm -rf /")` 拒（exit 126）。
- **SkillsMiddleware 发现**：fixture skill 注入 system prompt；state 隔离（orchestrator ≠ scene-agent 的 skills_metadata）。
- **CompositeBackend 路由**：`/skills/`、`/knowledge/`、`/prefs/`（StoreBackend）各达；`memory=/knowledge/AGENTS.md` 加载成功。
- **config 驱动**：`mode: docker` → factory 抛 NotImplementedError；`mode: local` → 返回 LocalBackend。
- **Windows 回归**：CI 跑 `WindowsLocalBackend`（或 mock subprocess）。

## 12. 风险与开放问题

- **WindowsLocalBackend 路线**（§5.3）：`LocalShellBackend` 子类 vs 移植 LX_AICoding `BaseSandbox`，spike 定。
- **约束 2 绕开验证**：`permissions=` + 可执行 backend + route-scoped paths 在 0.6.12 实测能否成立（`filesystem.py:736` 的 `_all_paths_scoped_to_routes` 判定），spike 期必须验证，否则路径权限方案要调整。
- **StoreBackend user 维度**：`namespace` 的 user_id 从哪来（请求 context？thread 绑定？）—— 本项目现无用户体系，Phase-1 可先按 thread_id 或单一固定 namespace，权限体系（Phase 5）再细化。
- **`execute` 走 default 的语义**（§7）：Phase-1 default=local → skill 脚本在本地执行（预期）。Phase-2 default=docker 时，所有 execute 进容器；本地文件读写仍路由 local——需确认 skill 脚本"在容器内跑但读本地脚本"的可行性（可能要把 skill 脚本上传进容器）。
- **白名单与 skill 脚本签名**：白名单收录 skill 脚本是按路径，未做内容校验；恶意 skill 包仍是风险（Phase-1 信任内置 skill；第三方包的签名/校验留 Phase 5）。

## 13. 与 v1 spec 的差异（变更摘要）

| 维度 | v1 | v2（本 spec） |
|---|---|---|
| Backend | 通用 FilesystemBackend | OS 自适应 LocalBackend（Win/Posix）+ Phase-2 Docker |
| Policy | 无（仅标风险） | 路径限制 + **命令真白名单** + 超时 + 审计 |
| 偏好 | 无 | StoreBackend |
| knowledge | FilesystemBackend | local backend（route） |
| 配置 | 硬编码 | `application.yaml` 配置式 |
| 部署 | 无 | install.sh/cmd |
| skills/ 位置 | src/space_aiagent/skills/ | project root |
| Skill 包 | SKILL.md | SKILL.md + scripts/references/assets |

## 14. 非范围 / 后续

- DockerSandboxBackend + install docker 分支 → Phase-2。
- skill-management skill（下载/分配生命周期，参考 ERP）→ 可纳入 SP3 或独立。
- 第三方 skill 签名/校验、skill 市场、版本管理、多租户 → Phase 5。
- 向量检索升级 → 远期。
