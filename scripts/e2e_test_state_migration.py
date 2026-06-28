"""端到端测试：state 迁移 + 自动续接 + subagent 路由

模拟前端 WebSocket 客户端，验证：
1. 主路径（流程 2）：用户「添加祝融号地面车」→ orchestrator task(entity-agent)
   → ToolValidationMiddleware NO_SCENE → PrimaryAgentMiddleware 捕获 intent
   → 用户「好的」→ orchestrator task(scene-agent) → create_scenario SCENE_CREATED
   → PrimaryAgentMiddleware 自动续接 task(entity-agent)
   → entity-agent 这次能从 state.current_scene_name 读到 → add_point_entity ENTITY_ADDED

启动方式：先 python -m space_aiagent.main，再 python scripts/e2e_test_state_migration.py
"""

import asyncio
import json
import os
import sys

import websockets


WS_URL = "ws://localhost:8028/ws/space"
THREAD_ID = f"e2e-state-migration-test-{os.urandom(2).hex()}"


async def mock_frontend(ws, expected_create_count: int, expected_add_count: int) -> None:
    """模拟前端：收到 tool_call 后返回成功 tool_result

    重要：必须等到 end 才退出（每轮 server 都会发 end），
    否则 end 残留在缓冲区被下一轮读取，导致下一轮立刻退出。
    """
    create_count = 0
    add_count = 0
    target_met = False
    async for raw in ws:
        msg = json.loads(raw)
        msg_type = msg.get("type")

        if msg_type == "tool_call":
            tool_func = msg["tool_func"]
            tool_call_id = msg["tool_call_id"]
            args = msg.get("tool_func_args", {})

            if tool_func == "createScenario":
                create_count += 1
                scene_name = args.get("sceneName", "默认场景")
                result = {
                    "type": "tool_result",
                    "thread_id": THREAD_ID,
                    "tool_call_id": tool_call_id,
                    "tool_func": tool_func,
                    "success": True,
                    "message": "场景创建成功",
                    "data": {"scene_name": scene_name},
                }
                print(f"[FRONT] createScenario #{create_count}: scene={scene_name}")
                await ws.send(json.dumps(result))
            elif tool_func == "addPointEntity":
                add_count += 1
                result = {
                    "type": "tool_result",
                    "thread_id": THREAD_ID,
                    "tool_call_id": tool_call_id,
                    "tool_func": tool_func,
                    "success": True,
                    "message": "实体添加成功",
                    "data": args,
                }
                print(f"[FRONT] addPointEntity #{add_count}: args={args}")
                await ws.send(json.dumps(result))
            else:
                print(f"[FRONT] unhandled tool_call: {tool_func}")

            if (
                not target_met
                and create_count >= expected_create_count
                and add_count >= expected_add_count
            ):
                target_met = True
                print("[FRONT] all expected tool calls received")
        elif msg_type == "ai_message":
            print(f"[AI] {msg.get('content', '')[:200]}")
        elif msg_type == "end":
            print("[END]")
            break
        elif msg_type == "error":
            print(f"[ERROR] {msg.get('message', '')}")
            break


async def send_user_input(ws, content: str, current_scene_name: str | None = None) -> None:
    msg = {
        "type": "user_input",
        "thread_id": THREAD_ID,
        "message_id": f"msg-{os.urandom(4).hex()}",
        "content": content,
        "current_scene_name": current_scene_name,
    }
    await ws.send(json.dumps(msg))


async def main():
    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL) as ws:
        # Round 1: 用户「添加文昌地面站」，无场景
        # 用文昌地面站（支持）而非祝融号地面车（OUT_OF_SCOPE）以触发 NO_SCENE 主路径
        print("\n=== Round 1: 添加文昌地面站（无场景，期望 NO_SCENE）===")
        await send_user_input(ws, "添加文昌地面站", current_scene_name=None)

        # 期望：orchestrator → task(entity-agent) → ToolValidationMiddleware NO_SCENE
        # → orchestrator 输出 AgentResponse(NO_SCENE)
        # entity-agent 的 ToolValidationMiddleware 在工具执行前就 NO_SCENE 短路，
        # 所以 addPointEntity 不会真的发到前端。等 end 即可。
        await mock_frontend(ws, expected_create_count=0, expected_add_count=0)
        print("Round 1 完成（期望 NO_SCENE）")

        # Round 2: 用户「创建一个新场景」（点击 Round1 的 suggestions[0]）
        # 触发 orchestrator → task(scene-agent) → create_scenario → SCENE_CREATED
        # → PrimaryAgentMiddleware 自动续接 task(entity-agent) → add_point_entity
        print("\n=== Round 2: 创建一个新场景（创建场景 + 自动续接，期望 ENTITY_ADDED）===")
        await send_user_input(ws, "创建一个新场景", current_scene_name=None)

        # 期望事件流：
        # 1. createScenario 到前端（用户同意创建场景，scene-agent 调用）
        # 2. 自动续接：addPointEntity 到前端
        await mock_frontend(ws, expected_create_count=1, expected_add_count=1)
        print("Round 2 完成（期望 ENTITY_ADDED）")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n中断")
        sys.exit(0)
