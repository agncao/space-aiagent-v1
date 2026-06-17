# Python 项目工程 Setup 指南

## 1. Python 两种常见的项目目录结构

**扁平结构（最常见，PyCharm 默认）**

```
my-project/
├── package_a/
│   ├── __init__.py
├── package_b/
│   ├── __init__.py
└── setup.py
```

**src-layout（部分大型项目采用）**

```
my-project/
├── src/
│   ├── package_a/
│   │   ├── __init__.py
│   ├── package_b/
│   │   ├── __init__.py
└── tests/
└── setup.py
```

src-layout 在 Python 中是可选的，主要用于解决导入隔离问题（防止未安装包时也能 import），但大多数中小项目都用扁平结构。

---

## 2. setup.py

setup.py 是 Python 的**打包配置文件**，用于将项目构建成一个可安装的 Python 包。核心作用：

1. **定义项目元信息**：名称、版本、作者、描述等
2. **声明依赖**：项目运行需要哪些第三方库
3. **让项目可以被 `pip install` 安装**：别人或你自己可以直接用 pip 安装你的项目

示例：

```python
from setuptools import setup, find_packages

setup(
    name="my-project",
    version="1.0.0",
    author="Your Name",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28",
        "openai>=1.0",
    ],
)
```

### 是否一定需要 setup.py？

取决于项目类型：

| 项目类型 | 是否需要 setup.py | 说明 |
|---------|-----------------|------|
| 可分发的库/SDK | **需要** | 别人要 pip install 你的包 |
| Web 服务/API | **可选** | 通常用 Docker 部署，不需要打包安装 |
| 内部工具脚本 | **通常不需要** | 直接运行即可 |
| 大型可部署项目 | **推荐有** | 方便管理依赖和版本 |

---

## 3. pyproject.toml（现代趋势）

现在 Python 社区更推荐用 **pyproject.toml** 替代 setup.py，它是新一代的标准（PEP 518/621）：

```toml
[project]
name = "my-project"
version = "1.0.0"
dependencies = [
    "requests>=2.28",
    "openai>=1.0",
]
```

pyproject.toml 一个文件同时管理：

- 项目元信息和依赖（原来 setup.py 的职责）
- 构建工具配置（如用 setuptools、hatchling 等）
- 其他工具配置（如 black、ruff 等）

### `[tool.setuptools.package-data]` — 打包非 Python 文件

默认情况下，`pip install` 只安装 `.py` 文件。如果项目中有非 Python 资源（提示词、配置模板、知识文件等），需要显式声明：

```toml
[tool.setuptools.package-data]
space_aiagent = ["prompts/*.md"]
```

这告诉 setuptools：**打包时把 `prompts/` 下的 `.md` 文件也打进去**。安装后这些文件会和 `.py` 文件一起放在 site-packages 中，代码通过 `Path(__file__)` 可以正常定位到它们。

**本项目的实际场景**：

```toml
# 之前的配置
[tool.setuptools.package-data]
space_aiagent = ["prompts/*.md", "knowledge/*.md"]

# 后来 knowledge/ 移到了 config/ 目录（外部化管理），不再打包进分发包
[tool.setuptools.package-data]
space_aiagent = ["prompts/*.md"]
```

**为什么不打包 knowledge？** knowledge 是领域知识（如 TLE 格式说明），可能需要运维在生产环境动态修改。放在 `config/` 下可以通过 ConfigMap 挂载覆盖，不用重新构建镜像。而 prompts 和代码强耦合（工具名、占位符等），适合打包在包内。

**常见需要打包的非 Python 文件**：

| 文件类型 | 示例 |
|---------|------|
| 提示词模板 | `prompts/*.md`、`prompts/*.txt` |
| 配置模板 | `templates/*.yaml` |
| 数据文件 | `data/*.json`、`data/*.csv` |
| 静态资源 | `static/*.html`、`static/*.css` |

**Java 对比**：类似 Maven 中 `src/main/resources/` 下的文件会自动打进 JAR——`package-data` 就是告诉 Python 打包工具"这些非代码资源也要打包"。

### `[project.scripts]` — 注册命令行入口

`[project.scripts]` 把一个 Python 函数注册为系统命令，`pip install` 后可以直接在终端使用。

```toml
[project.scripts]
space-aiagent = "space_aiagent.cli:main"
#   ↑ 命令名          ↑ 模块路径:函数名
```

安装后 setuptools 会自动生成一个可执行脚本，相当于：

```python
#!/usr/bin/env python
from space_aiagent.cli import main
main()
```

然后就能直接在终端使用：

```bash
space-aiagent --help
space-aiagent run --reload
space-aiagent skills list
```

**不写会怎样？**

打包不会出错，运行也不会出错。只是不能直接敲命令名，得换种方式启动：

```bash
# 有 [project.scripts] 时：
space-aiagent run                    # ✅ 直接用命令名

# 没有时，用 -m 指定模块（效果完全一样，只是命令更长）：
python -m space_aiagent.main         # ✅ 也能运行
python -m space_aiagent.cli run      # ✅ 同上
```

所以 `[project.scripts]` **不是必须的**，只是一个便捷入口。对于需要分发给其他人用的 CLI 工具很有用；对于纯 Web 服务（用 Docker/uvicorn 部署），不写也没影响。

**Java 对比**：

| Python | Java (Spring Boot) |
|--------|-------------------|
| `[project.scripts]` 注册命令 | Maven 打包生成的启动脚本 |
| `space-aiagent run` | `java -jar app.jar` |
| `python -m xxx`（无 scripts 时） | `java -cp app.jar com.xxx.Main` |

---

## 4. 依赖管理方案对比

### 方案一：只用 pyproject.toml（最现代）

```toml
[project]
name = "my-project"
version = "1.0.0"
dependencies = [
    "requests>=2.28",
    "openai>=1.0",
]
```

配套工具：`pip install .` 或用包管理器如 **uv**、**poetry**、**hatch** 来管理依赖。

不需要 requirements.txt，依赖声明全在 pyproject.toml 里。

### 方案二：pyproject.toml + requirements.txt（最常见）

```
pyproject.toml    → 定义项目元信息、构建配置、开发工具配置
requirements.txt  → pip install -r requirements.txt 安装依赖
```

这是目前生产环境中最广泛的组合，原因：

- pip 仍然是最主流的安装方式
- CI/CD、Docker 构建中 `pip install -r requirements.txt` 是标准操作
- 团队中所有人都能用，不需要额外学 poetry/uv 等工具
- pyproject.toml 负责项目元信息，requirements.txt 负责锁定依赖版本

### 方案三：pyproject.toml + lock 文件（高级）

用 Poetry 或 uv 这类现代工具时：

```
pyproject.toml   → 声明依赖范围
poetry.lock      → 锁定精确版本（类似 requirements.txt 的作用但更强）
```

这种方式也不需要 requirements.txt。

### 综合对比

| 方案 | 适合场景 | 复杂度 |
|------|---------|--------|
| 只有 pyproject.toml | 简单项目 / 个人项目 | 低 |
| pyproject.toml + requirements.txt | **生产项目主流** | 中 |
| pyproject.toml + lock 文件 | 用 poetry/uv 管理的项目 | 中高 |

---

## 5. 当前项目建议

对于本学习项目（aiagent-study），现阶段不需要 setup.py 或 pyproject.toml。

当你需要把项目打包分发、或者正式管理依赖时，推荐先用 `pyproject.toml + requirements.txt`，这是最务实、最广泛兼容的组合。

等熟悉后可以尝试 uv 或 poetry 来体验更现代的方式。