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
    """
    启动 Web 服务器

    步骤:
    1. 加载配置
    2. 初始化日志
    3. 启动 uvicorn

    TODO: 实现
    """
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
    """
    列出所有已注册的 Skill

    步骤:
    1. 创建 SkillRegistry 并扫描
    2. 打印每个 Skill 的名称和描述

    TODO: 实现
    """
    from space_aiagent.skills import SkillRegistry
    registry = SkillRegistry()
    registry.discover()
    for summary in registry.get_summaries():
        click.echo(f"  {summary['name']}: {summary['description']}")


@skills.command("show")
@click.argument("name")
def skills_show(name: str) -> None:
    """
    查看指定 Skill 的详细信息

    步骤:
    1. 从注册表获取 SkillInfo
    2. 打印名称、描述、触发词、工具列表

    TODO: 实现
    """
    click.echo(f"Skill: {name}")
    click.echo("TODO: 实现")


if __name__ == "__main__":
    main()
