"""
space-aiagent CLI 入口

提供命令行管理工具。

用法:
    space-aiagent --help
    space-aiagent run              # 启动服务器
    space-aiagent skills list      # 列出所有 Skill
    space-aiagent skills show <n>  # 查看某个 Skill 的详情
"""

import click


@click.group()
@click.version_option(version="0.1.0")
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
def skills() -> None:
    """Skill 管理命令"""
    pass


@skills.command("list")
def skills_list() -> None:
    """列出所有已注册的 Skill"""
    from space_aiagent.skills import SkillRegistry

    registry = SkillRegistry()
    registry.discover()
    summaries = registry.get_summaries()
    if not summaries:
        click.echo("暂无已注册的 Skill")
        return
    for summary in summaries:
        click.echo(f"  {summary['name']}: {summary['description']}")


@skills.command("show")
@click.argument("name")
def skills_show(name: str) -> None:
    """查看指定 Skill 的详细信息"""
    from space_aiagent.skills import SkillLoader, SkillRegistry

    registry = SkillRegistry()
    registry.discover()
    info = registry.get_skill(name)
    if info is None:
        click.echo(f"Skill 不存在: {name}")
        click.echo(f"可用 Skill: {', '.join(registry.list_skill_names())}")
        return

    click.echo(f"名称: {info.name}")
    click.echo(f"描述: {info.description}")
    click.echo(f"触发词: {', '.join(info.triggers)}")
    click.echo(f"目录: {info.skill_dir}")

    # 加载工具列表
    loader = SkillLoader(registry)
    tools = loader.load_skill(name)
    if tools:
        click.echo(f"工具 ({len(tools)} 个):")
        for t in tools:
            click.echo(f"  - {t.name}: {t.description}")
    else:
        click.echo("工具: （无）")


if __name__ == "__main__":
    main()
