"""
space-aiagent CLI 入口

提供命令行管理工具。

用法:
    space-aiagent --help
    space-aiagent run              # 启动服务器
    space-aiagent tools list       # 列出所有工具组
    space-aiagent tools show <n>   # 查看某个工具组的详情
"""

import click

from space_aiagent import __version__


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """航天分析平台智能助手"""
    pass


@main.command()
@click.option("--host", default="0.0.0.0", help="服务器地址")
@click.option("--port", default=8028, help="服务器端口")
@click.option("--reload", is_flag=True, help="启用热重载")
def run(host: str, port: int, reload: bool) -> None:
    """启动 Web 服务器"""
    import uvicorn

    uvicorn.run(
        "space_aiagent.main:app",
        host=host,
        port=port,
        reload=reload,
    )


@main.group()
def tools() -> None:
    """工具组管理命令"""
    pass


def _build_group_descriptions_from_yaml() -> dict[str, str]:
    """从 workers.yaml 反查工具组到 Worker 描述的映射。"""
    import yaml

    from space_aiagent.infrastructure.config import CONFIG_DIR

    config_path = CONFIG_DIR / "workers.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    group_desc: dict[str, str] = {}
    for worker in config.get("workers", []):
        for group in worker.get("tools", []):
            if group not in group_desc:
                group_desc[group] = worker.get("description", "")
    return group_desc


@tools.command("list")
def tools_list() -> None:
    """列出所有已注册工具组"""
    from space_aiagent.tools.registry import get_all_groups

    groups = get_all_groups()
    group_desc = _build_group_descriptions_from_yaml()
    if not groups:
        click.echo("暂无已注册的工具组")
        return
    for name, tool_list in groups.items():
        desc = group_desc.get(name, "（描述见 workers.yaml）")
        click.echo(f"  {name} ({len(tool_list)} 个工具): {desc}")


@tools.command("show")
@click.argument("name")
def tools_show(name: str) -> None:
    """查看指定工具组的详细信息"""
    from space_aiagent.tools.registry import get_all_groups

    groups = get_all_groups()
    if name not in groups:
        click.echo(f"工具组不存在: {name}")
        click.echo(f"可用工具组: {', '.join(groups.keys())}")
        return

    group_desc = _build_group_descriptions_from_yaml()
    click.echo(f"名称: {name}")
    click.echo(f"描述: {group_desc.get(name, '（描述见 workers.yaml）')}")

    tool_list = groups[name]
    if tool_list:
        click.echo(f"工具 ({len(tool_list)} 个):")
        for t in tool_list:
            click.echo(f"  - {t.name}: {t.description}")
    else:
        click.echo("工具: （无）")


if __name__ == "__main__":
    main()
