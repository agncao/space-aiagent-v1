"""Skill 注册表测试"""


def test_registry_init():
    """测试注册表初始化"""
    from space_aiagent.skills.registry import SkillRegistry
    registry = SkillRegistry()
    assert registry.list_skill_names() == []


# TODO: 添加更多测试
# - test_discover: 测试 Skill 扫描
# - test_get_summaries: 测试摘要获取
# - test_get_skill: 测试按名称获取
