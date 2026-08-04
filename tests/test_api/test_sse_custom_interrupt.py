"""``_handle_interrupts`` 自定义中断透传单测

验证 SSE 传输层对三类 interrupt payload 的分发（api/sse.py:_handle_interrupts）：

- ``is_custom=True``（SceneAgentHitlMiddleware 产出的自定义中断）→ 四件套透传
  （is_custom + interrupt_type + message + data），前端按 interrupt_type 渲染。
  覆盖 hitl_select（场景选择，带候选列表）与 hitl_yn（保存确认）两个真实形态。
- ``action_requests``（声明式 interrupt_on / HumanInTheLoopMiddleware）→ hitl_approval，
  ``is_custom=False``，透传 action_requests / review_configs。
- 其它 → unknown 透传（截断 2000 字符），``is_custom=False``，保证不挂死。

每个 interrupt 发一帧 INTERRUPT，最后发一帧终态 DONE(interrupted=True)。
"""

from space_aiagent.api.sse import _handle_interrupts
from space_aiagent.models.sse_events import SSEEventType


class _FakeBridge:
    """记录 _emit 调用，等价于 StreamBridge 的出口（不依赖 asyncio.Queue / Future）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[SSEEventType, dict]] = []

    async def _emit(self, event_type: SSEEventType, payload: dict) -> None:
        self.calls.append((event_type, payload))


def _interrupt(value: dict):
    """模拟 LangGraph Interrupt 对象：.value 是传给 interrupt() 的 dict。"""
    return type("Interrupt", (), {"value": value})()


def _interrupt_frames(bridge: _FakeBridge) -> list[dict]:
    """取所有 INTERRUPT 帧的 payload。"""
    return [p for et, p in bridge.calls if et == SSEEventType.INTERRUPT]


def _has_terminal_done(bridge: _FakeBridge) -> bool:
    """末帧应为终态 DONE(interrupted=True)。"""
    return any(
        et == SSEEventType.DONE and p.get("interrupted") is True
        for et, p in bridge.calls
    )


# ── is_custom 自定义中断：四件套透传 ────────────────────────────────────


async def test_custom_select_interrupt_passthrough():
    """hitl_select（多场景选择）→ 四件套透传，data 带候选列表原样下沉。"""
    bridge = _FakeBridge()
    candidates = [
        {"scene_name": "测试场景A", "update_time": "2026-08-01", "uploader_name": "u1"},
        {"scene_name": "测试场景B", "update_time": "2026-08-02", "uploader_name": "u2"},
    ]

    await _handle_interrupts(
        bridge,
        [
            _interrupt(
                {
                    "is_custom": True,
                    "interrupt_type": "hitl_select",
                    "message": "找到多个匹配场景，请选择要打开的场景：",
                    "data": {"scene_info_list": candidates},
                }
            )
        ],
    )

    frames = _interrupt_frames(bridge)
    assert len(frames) == 1
    payload = frames[0]
    assert payload["is_custom"] is True
    assert payload["interrupt_type"] == "hitl_select"
    assert payload["message"] == "找到多个匹配场景，请选择要打开的场景："
    assert payload["data"]["scene_info_list"] == candidates
    assert _has_terminal_done(bridge)


async def test_custom_yn_interrupt_passthrough():
    """hitl_yn（未保存变更确认）→ 四件套透传，data 带当前/目标场景名。"""
    bridge = _FakeBridge()

    await _handle_interrupts(
        bridge,
        [
            _interrupt(
                {
                    "is_custom": True,
                    "interrupt_type": "hitl_yn",
                    "message": "当前场景存在未保存的变更，是否在切换前保存？(Y/N)",
                    "data": {"scene_name": "当前场景", "target_scene_name": "目标场景"},
                }
            )
        ],
    )

    payload = _interrupt_frames(bridge)[0]
    assert payload["is_custom"] is True
    assert payload["interrupt_type"] == "hitl_yn"
    assert payload["data"]["target_scene_name"] == "目标场景"


async def test_custom_missing_fields_default_safe():
    """自定义中断缺 interrupt_type/message/data → 安全降级，不抛异常。"""
    bridge = _FakeBridge()

    await _handle_interrupts(bridge, [_interrupt({"is_custom": True})])

    payload = _interrupt_frames(bridge)[0]
    assert payload["is_custom"] is True
    assert payload["interrupt_type"] == "unknown"  # 缺省
    assert payload["message"] == ""
    assert payload["data"] is None


# ── 声明式 hitl_approval：is_custom=False ───────────────────────────────


async def test_declarative_approval_passthrough():
    """action_requests → hitl_approval，is_custom=False，透传 review_configs。"""
    bridge = _FakeBridge()
    action_requests = [{"title": "delete_scene", "args": {"scene_name": "A"}}]
    review_configs = [{"type": "approve", "description": "确认删除"}]

    await _handle_interrupts(
        bridge,
        [_interrupt({"action_requests": action_requests, "review_configs": review_configs})],
    )

    payload = _interrupt_frames(bridge)[0]
    assert payload["is_custom"] is False
    assert payload["interrupt_type"] == "hitl_approval"
    assert payload["action_requests"] == action_requests
    assert payload["review_configs"] == review_configs


# ── unknown 兜底 ────────────────────────────────────────────────────────


async def test_unknown_interrupt_truncated_passthrough():
    """无法识别的 payload → unknown 透传，is_custom=False，值截断 2000 字符。"""
    bridge = _FakeBridge()
    weird = "x" * 5000

    await _handle_interrupts(bridge, [_interrupt({"some_unexpected_key": weird})])

    payload = _interrupt_frames(bridge)[0]
    assert payload["is_custom"] is False
    assert payload["interrupt_type"] == "unknown"
    assert len(payload["interrupt_value"]) <= 2000


# ── 多中断一帧不漏 + 终态收尾 ───────────────────────────────────────────


async def test_multiple_interrupts_each_get_a_frame():
    """一次暂停含多个中断 → 每个各发一帧 INTERRUPT，最后单个终态 DONE。"""
    bridge = _FakeBridge()

    await _handle_interrupts(
        bridge,
        [
            _interrupt({"is_custom": True, "interrupt_type": "hitl_select", "message": "m1", "data": None}),
            _interrupt({"action_requests": [], "review_configs": []}),
        ],
    )

    frames = _interrupt_frames(bridge)
    assert len(frames) == 2
    assert frames[0]["interrupt_type"] == "hitl_select"
    assert frames[1]["interrupt_type"] == "hitl_approval"
    # 终态 DONE 恰好一个
    done_count = sum(1 for et, _ in bridge.calls if et == SSEEventType.DONE)
    assert done_count == 1
