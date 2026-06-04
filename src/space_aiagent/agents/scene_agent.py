"""
场景子 Agent（Scene Agent）

职责:
1. 处理所有场景相关操作
2. 动态加载 scene_management Skill 的工具
3. 通过远程工具桥接调用前端 Cesium 操作

实现步骤:
1. 使用 deepagents.create_deep_agent 创建子 Agent
2. 绑定 scene_management Skill 的工具
3. 工具通过 bridge（WebSocket）发送指令到前端执行
4. 处理前端返回的结果

工具列表（scene_management Skill）:
- create_scenario: 创建场景
- rename_scenario: 重命名场景
- clear_scene: 清除场景
- clear_entities: 清除实体
- query_scenario: 查询场景
- query_scenario_entities: 查询实体
"""
from space_aiagent.skills import SkillLoader


def create_scene_agent(skill_loader: SkillLoader):
    """
    创建场景管理子 Agent

    步骤:
    1. 通过 skill_loader.load_skill("scene_management") 获取工具
    2. 使用 deepagents.create_deep_agent 创建 Agent
    3. 绑定工具和 system prompt
    4. 返回 Agent 实例

    TODO: 实现
    """
    # tools = skill_loader.load_skill("scene_management")
    # agent = create_deep_agent(
    #     model=model_string,
    #     tools=tools,
    #     system_prompt=SCENE_AGENT_PROMPT,
    # )
    pass


SCENE_AGENT_PROMPT = """你是航天分析平台的场景管理专家。

你的职责是管理航天场景的创建、查询、重命名和清除。

你可以使用以下工具:
- create_scenario: 创建新的航天场景
- rename_scenario: 重命名当前场景
- clear_scene: 清除当前场景的所有内容
- clear_entities: 清除场景中的所有实体
- query_scenario: 查询场景信息
- query_scenario_entities: 查询场景中的实体列表

注意事项:
- 每次操作前确认场景状态
- 清除操作需要用户确认
- 用中文回复
"""
