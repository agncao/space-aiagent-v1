"""SpaceAgentState 自定义 Reducer 测试。"""

from langgraph.graph import END, START, StateGraph

from space_aiagent.agents.state import SpaceAgentState, _keep_last_scene_name, update_tool_result


def test_update_tool_result_deduplicates_when_appending() -> None:
    current = [{"scene_name": "火箭场景"}]
    update = [
        {"scene_name": "火箭场景"},
        {"scene_name": "卫星场景"},
    ]

    assert update_tool_result(current, update) == [
        {"scene_name": "火箭场景"},
        {"scene_name": "卫星场景"},
    ]


def test_state_graph_accepts_concurrent_tool_result_updates() -> None:
    def query_current(_: SpaceAgentState) -> dict:
        return {"scenario_query_results": [{"scene_name": "火箭场景"}]}

    def query_named(_: SpaceAgentState) -> dict:
        return {
            "scenario_query_results": [
                {"scene_name": "火箭场景"},
                {"scene_name": "火箭测试场景"},
            ]
        }

    graph_builder = StateGraph(SpaceAgentState)
    graph_builder.add_node("query_current", query_current)
    graph_builder.add_node("query_named", query_named)
    graph_builder.add_edge(START, "query_current")
    graph_builder.add_edge(START, "query_named")
    graph_builder.add_edge("query_current", END)
    graph_builder.add_edge("query_named", END)
    graph = graph_builder.compile()

    result = graph.invoke({"messages": [], "scenario_query_results": None})

    assert result["scenario_query_results"] == [
        {"scene_name": "火箭场景"},
        {"scene_name": "火箭测试场景"},
    ]


def test_none_clears_results_before_reducer_appends() -> None:
    def reset_results(_: SpaceAgentState) -> dict:
        return {"scenario_query_results": None}

    def query_new(_: SpaceAgentState) -> dict:
        return {"scenario_query_results": [{"scene_name": "新场景"}]}

    graph_builder = StateGraph(SpaceAgentState)
    graph_builder.add_node("reset_results", reset_results)
    graph_builder.add_node("query_new", query_new)
    graph_builder.add_edge(START, "reset_results")
    graph_builder.add_edge("reset_results", "query_new")
    graph_builder.add_edge("query_new", END)
    graph = graph_builder.compile()

    result = graph.invoke(
        {
            "messages": [],
            "scenario_query_results": [{"scene_name": "历史场景"}],
        }
    )

    assert result["scenario_query_results"] == [{"scene_name": "新场景"}]


def test_keep_last_scene_name_returns_right_value() -> None:
    assert _keep_last_scene_name("旧场景", "新场景") == "新场景"


def test_state_graph_accepts_concurrent_current_scene_name_updates() -> None:
    """两个并发查询同时写 current_scene_name 时不应崩溃（last-write-wins）。"""

    def query_a(_: SpaceAgentState) -> dict:
        return {"current_scene_name": "场景A"}

    def query_b(_: SpaceAgentState) -> dict:
        return {"current_scene_name": "场景B"}

    graph_builder = StateGraph(SpaceAgentState)
    graph_builder.add_node("query_a", query_a)
    graph_builder.add_node("query_b", query_b)
    graph_builder.add_edge(START, "query_a")
    graph_builder.add_edge(START, "query_b")
    graph_builder.add_edge("query_a", END)
    graph_builder.add_edge("query_b", END)
    graph = graph_builder.compile()

    result = graph.invoke({"messages": []})

    # 并发下 last-write-wins，最终值为两者之一，不应抛 INVALID_CONCURRENT_GRAPH_UPDATE。
    assert result["current_scene_name"] in {"场景A", "场景B"}
