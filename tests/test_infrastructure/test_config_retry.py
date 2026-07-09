"""RetryConfig 加载测试"""

from space_aiagent.infrastructure.config import RetryConfig, RetryLLMConfig


def test_retry_config_defaults():
    """默认值：enabled=true，llm/tool max_attempts=3，retry_on_parse_error=false"""
    cfg = RetryConfig()
    assert cfg.enabled is True
    assert cfg.llm.max_attempts == 3
    assert cfg.llm.base_delay == 1.0
    assert cfg.llm.max_delay == 10.0
    assert cfg.llm.retry_on_parse_error is False
    assert cfg.tool.max_attempts == 3
    assert cfg.tool.base_delay == 1.0
    assert cfg.tool.max_delay == 10.0


def test_retry_config_retry_on_parse_error_can_be_enabled():
    cfg = RetryConfig(llm=RetryLLMConfig(retry_on_parse_error=True))
    assert cfg.llm.retry_on_parse_error is True


def test_retry_config_loaded_from_yaml():
    """application.yaml 的 retry 段能被 _apply_yaml_to_settings 正确读取"""
    from space_aiagent.infrastructure.config import _apply_yaml_to_settings

    yaml_config = {
        "retry": {
            "enabled": False,
            "llm": {"max_attempts": 5, "base_delay": 2.0, "max_delay": 30.0, "retry_on_parse_error": True},
            "tool": {"max_attempts": 2, "base_delay": 0.5, "max_delay": 5.0},
        }
    }
    settings = _apply_yaml_to_settings(yaml_config)
    assert settings.retry.enabled is False
    assert settings.retry.llm.max_attempts == 5
    assert settings.retry.llm.retry_on_parse_error is True
    assert settings.retry.tool.max_attempts == 2
