"""build_model 单测

验证 ChatOpenAI 构造参数：
- extra_body 必须透传 enable_thinking。DashScope Qwen3 默认开启 thinking mode，
  与 ToolStrategy 强制的 tool_choice=required 互斥（HTTP 400
  InternalError.Algo.InvalidParameter）。必须显式 enable_thinking=false 才能与
  ToolStrategy 共存。
- model_kwargs 必须禁用 parallel_tool_calls。scene 工具成功路径返回
  Command(update={"scenario_query_results"|"current_scene_name": ...})，这两个
  SpaceAgentState 字段是 last_value 通道（无 reducer）。LLM 并行发起两个工具调用
  时，同一 step 对 last_value 通道写入两次会抛 InvalidUpdateError
  (Can receive only one value per step)。回归用例见此文件下方。
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


def test_build_model_disables_parallel_tool_calls():
    """必须通过 model_kwargs 关闭并行工具调用。

    回归：scene-agent 曾因 LLM 一次响应并行发起两个 query_scenario（scene_name='火箭'
    与 'null'）触发 InvalidUpdateError: At key 'scenario_query_results': Can receive
    only one value per step。两个工具成功路径都 Command(update={"scenario_query_results":
    ...}) 写同一个 last_value 通道。关闭 parallel_tool_calls 从根上消除该冲突。
    """
    with (
        patch.object(llm_module, "get_settings", return_value=_fake_settings(False)),
        patch.object(llm_module, "ChatOpenAI") as fake_ctor,
    ):
        llm_module.build_model()

    _, kwargs = fake_ctor.call_args
    assert kwargs["model_kwargs"] == {"parallel_tool_calls": False}
