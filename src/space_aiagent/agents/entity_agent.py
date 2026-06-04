"""
实体子 Agent（Entity Agent）

职责:
1. 处理所有实体和轨道相关操作
2. 动态加载 entity_management 和 orbit_management Skill 的工具
3. 通过远程工具桥接调用前端 Cesium 操作

实现步骤:
1. 使用 deepagents.create_deep_agent 创建子 Agent
2. 绑定 entity_management + orbit_management Skill 的工具
3. 工具通过 bridge（WebSocket）发送指令到前端执行
4. 处理前端返回的结果

工具列表:
  entity_management Skill:
  - add_point_entity: 添加点实体

  orbit_management Skill:
  - create_sgp4_orbit: 创建 SGP4 轨道
  - update_sgp4_orbit: 更新轨道样式

前置条件: 场景必须已创建
"""
from space_aiagent.skills import SkillLoader


def create_entity_agent(skill_loader: SkillLoader):
    """
    创建实体管理子 Agent

    步骤:
    1. 通过 skill_loader.load_skills 加载两个 Skill 的工具:
       - entity_management
       - orbit_management
    2. 使用 deepagents.create_deep_agent 创建 Agent
    3. 绑定工具和 system prompt
    4. 返回 Agent 实例

    TODO: 实现
    """
    # tools = skill_loader.load_skills(["entity_management", "orbit_management"])
    # agent = create_deep_agent(
    #     model=model_string,
    #     tools=tools,
    #     system_prompt=ENTITY_AGENT_PROMPT,
    # )
    pass


ENTITY_AGENT_PROMPT = """你是航天分析专家。

你的职责是基于任务场景创管理各类实体（卫星、地面站等）并进行航天任务的执行。

你可以使用以下工具:
- add_point_entity: 在场景中添加点实体（卫星、地面站、传感器等）
- create_sgp4_orbit: 基于 SGP4 模型创建卫星轨道
- update_sgp4_orbit: 更新卫星轨道的显示样式

重要规则:
- 添加实体前必须确保场景已创建
- 如果场景未创建，提示用户先创建场景
- SGP4 轨道需要正确的 TLE 两行根数数据
- 用中文回复
"""
