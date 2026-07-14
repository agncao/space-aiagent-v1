"""build_model 单测

验证 ChatOpenAI 构造参数：
- extra_body 必须透传 enable_thinking。DashScope Qwen3 默认开启 thinking mode，
  与 ToolStrategy 强制的 tool_choice=required 互斥（HTTP 400
  InternalError.Algo.InvalidParameter）。必须显式 enable_thinking=false 才能与
  ToolStrategy 共存。
"""

from types import SimpleNamespace
from unittest.mock import patch

from space_aiagent.infrastructure import llm as llm_module


def _fake_settings(enable_thinking: bool) -> SimpleNamespace:
    return SimpleNamespace(
        llm=SimpleNamespace(
            model="qwen3.7-max-preview",
            api_key="fake-key",
            base_url="https://dashscope.example/v1",
            temperature=0,
            streaming=True,
            enable_thinking=enable_thinking,
        ),
        llm_flash=SimpleNamespace(
            model="qwen3.7-flash-preview",
            api_key="fake-key",
            base_url="https://dashscope.example/v1",
            temperature=0,
            streaming=True,
            enable_thinking=enable_thinking,
        ),
    )


def test_build_model_passes_enable_thinking_false_via_extra_body():
    """settings.llm.enable_thinking=False 必须透传到 ChatOpenAI extra_body"""
    with (
        patch.object(llm_module, "get_settings", return_value=_fake_settings(False)),
        patch.object(llm_module, "ChatOpenAI") as fake_ctor,
    ):
        llm_module.build_model()

    _, kwargs = fake_ctor.call_args
    assert kwargs["extra_body"] == {"enable_thinking": False}


def test_build_model_passes_enable_thinking_true_via_extra_body():
    """settings.llm.enable_thinking=True 同样必须透传，保证配置开关可逆"""
    with (
        patch.object(llm_module, "get_settings", return_value=_fake_settings(True)),
        patch.object(llm_module, "ChatOpenAI") as fake_ctor,
    ):
        llm_module.build_model()

    _, kwargs = fake_ctor.call_args
    assert kwargs["extra_body"] == {"enable_thinking": True}
