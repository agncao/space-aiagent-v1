# Space AIAgent Windows 服务器部署指南

> 面向航天研究院 Windows 服务器环境，不依赖 Docker，直接部署源码运行。

---

## 1. 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows Server 2016+ / Windows 10+ |
| Python | 3.13+（推荐 Miniconda） |
| 内存 | ≥ 4GB |
| 磁盘 | ≥ 2GB（含依赖包） |
| 网络 | 需要访问 LLM API（DeepSeek / Qwen） |

---

## 2. 安装 Python 环境

### 方式一：Miniconda（推荐）

```powershell
# 1. 下载 Miniconda 安装包
# https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Windows-x86_64.exe
# （清华镜像源，国内下载快）

# 2. 安装时勾选 "Add Miniconda to PATH"

# 3. 打开 Anaconda Prompt，创建虚拟环境
conda create -n space-aiagent-v1 python=3.13 -y
conda activate space-aiagent-v1
```

### 方式二：官方 Python

```powershell
# 1. 下载 Python 3.13
# https://www.python.org/downloads/
# 安装时务必勾选 "Add Python to PATH"

# 2. 创建虚拟环境
cd C:\space-aiagent-v1
python -m venv .venv
.venv\Scripts\activate
```

---

## 3. 部署项目

### 3.1 拷贝源码

```powershell
# 将项目拷贝到服务器（U盘 / 内网Git / SCP 均可）
# 目标目录：C:\space-aiagent-v1

# 最终目录结构：
C:\space-aiagent-v1\
├── src\space_aiagent\            # 源码
├── config\                       # 配置文件
│   ├── application.yaml
│   ├── dev.yaml
│   ├── subagents.yaml
│   └── knowledge\
│       └── AGENTS.md
├── .env                          # 环境变量（需手动创建）
├── pyproject.toml
└── requirements.txt
```

### 3.2 安装依赖

```powershell
cd C:\space-aiagent-v1

# 激活环境
conda activate space-aiagent-v1

# 方式一：通过 pyproject.toml 安装（推荐）
pip install -e ".[dev]"

# 方式二：通过 requirements.txt 安装
pip install -r requirements.txt

# 验证安装
python -c "from space_aiagent.infrastructure.config import get_settings; print('安装成功')"
```

### 3.3 配置环境变量

复制 `.env.example` 为 `.env`，填写实际值：

```powershell
copy .env.example .env
notepad .env
```

```ini
# .env 文件内容
APP_ENV=prod

# LLM 配置（必须填写）
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 日志
LOG_LEVEL=INFO
LOG_DIR=./logs
```

> **注意**：`.env` 文件包含 API Key 等敏感信息，不要提交到 Git 或共享给无关人员。

### 3.4 验证启动

```powershell
python -m space_aiagent.main
```

看到以下输出说明启动成功：

```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8028
```

浏览器访问 `http://localhost:8028/api/v1/space/health`，应返回：

```json
{"status": "ok", "service": "space-aiagent"}
```

---

## 4. 启动脚本

### 4.1 手动启动（开发/测试）

创建 `run.bat`：

```bat
@echo off
:: 航天分析平台智能助手 - 启动脚本

:: 激活 Conda 环境
call C:\ProgramData\miniconda3\condabin\conda.bat activate space-aiagent-v1

:: 切换到项目目录（确保 os.getcwd() 和 Path(__file__) 都正确）
cd /d C:\space-aiagent-v1

:: 启动服务
python -m space_aiagent.main

pause
```

双击 `run.bat` 即可启动。

### 4.2 带日志的启动脚本

```bat
@echo off
:: 航天分析平台智能助手 - 带日志启动

call C:\ProgramData\miniconda3\condabin\conda.bat activate space-aiagent-v1
cd /d C:\space-aiagent-v1

:: 将输出同时显示在控制台和写入日志文件
python -m space_aiagent.main 2>&1 | tee logs\startup.log

pause
```

---

## 5. 注册为 Windows 服务（生产环境推荐）

使用 **NSSM**（Non-Sucking Service Manager）将应用注册为 Windows 服务，实现开机自启和崩溃自动重启。

### 5.1 安装 NSSM

```powershell
# 方式一：下载exe
# https://nssm.cc/download
# 将 nssm.exe 放到 C:\Windows\System32\ 或任意 PATH 目录

# 方式二：Chocolatey（如果已安装）
choco install nssm
```

### 5.2 创建启动脚本

创建 `service.bat`（不带 `pause`，适合服务模式）：

```bat
@echo off
call C:\ProgramData\miniconda3\condabin\conda.bat activate space-aiagent-v1
cd /d C:\space-aiagent-v1
python -m space_aiagent.main
```

### 5.3 注册服务

```powershell
# 以管理员身份运行 PowerShell

# 安装服务
nssm install SpaceAIAgent C:\space-aiagent-v1\service.bat

# 配置服务
nssm set SpaceAIAgent AppDirectory C:\space-aiagent-v1
nssm set SpaceAIAgent DisplayName "航天分析平台智能助手"
nssm set SpaceAIAgent Description "基于 DeepAgent 的多 Agent 智能助手服务"
nssm set SpaceAIAgent StartServiceNameLinger 0

# 日志输出（将 stdout/stderr 重定向到文件）
nssm set SpaceAIAgent AppStdout C:\space-aiagent-v1\logs\service-stdout.log
nssm set SpaceAIAgent AppStderr C:\space-aiagent-v1\logs\service-stderr.log

# 日志轮转（避免日志文件无限增长）
nssm set SpaceAIAgent AppRotateFiles 1
nssm set SpaceAIAgent AppRotateBytes 10485760

# 启动服务
nssm start SpaceAIAgent
```

### 5.4 管理服务

```powershell
nssm start SpaceAIAgent       # 启动
nssm stop SpaceAIAgent        # 停止
nssm restart SpaceAIAgent     # 重启
nssm status SpaceAIAgent      # 查看状态
nssm edit SpaceAIAgent        # 打开图形化编辑器
nssm remove SpaceAIAgent      # 删除服务（先 stop）
```

注册成功后，在 `services.msc` 中可以看到"航天分析平台智能助手"服务，支持开机自启。

---

## 6. 更新部署

```powershell
cd C:\space-aiagent-v1

# 1. 停止服务
nssm stop SpaceAIAgent

# 2. 更新源码（U盘覆盖 / Git pull）
git pull origin master
# 或直接覆盖 src\ 目录

# 3. 更新依赖（如果 pyproject.toml 有变化）
conda activate space-aiagent-v1
pip install -e ".[dev]"

# 4. 启动服务
nssm start SpaceAIAgent
```

---

## 7. 防火墙配置

如果前端部署在其他机器上，需要开放端口：

```powershell
# 以管理员身份运行，开放 8028 端口
netsh advfirewall firewall add rule name="SpaceAIAgent" dir=in action=allow protocol=TCP localport=8028

# 验证规则
netsh advfirewall firewall show rule name="SpaceAIAgent"
```

---

## 8. 常见问题

### 启动报错 `ModuleNotFoundError`

```powershell
# 确认虚拟环境已激活
conda activate space-aiagent-v1

# 确认依赖已安装
pip install -e ".[dev]"
```

### 启动报错 `LLM_API_KEY not set`

`.env` 文件不存在或未填写 API Key。检查：

```powershell
# .env 文件必须在项目根目录下
dir C:\space-aiagent-v1\.env

# 确认内容
type C:\space-aiagent-v1\.env
```

### 端口被占用

```powershell
# 查看占用 8028 端口的进程
netstat -ano | findstr :8028

# 结束占用进程（PID 从上一步获取）
taskkill /PID <PID> /F

# 或者修改 .env 中的端口
# SERVER_PORT=8029
```

### 服务启动后立即停止

```powershell
# 查看错误日志
type C:\space-aiagent-v1\logs\service-stderr.log

# 常见原因：
# 1. Conda 环境路径不对 → 检查 service.bat 中 conda 路径
# 2. .env 文件缺失 → 检查步骤 3.3
# 3. API Key 无效 → 联系运维获取正确的 Key
```

### 数据库文件在哪里

```
C:\space-aiagent-v1\data\space_aiagent.db
```

不要删除此文件，否则会丢失会话历史。

---

## 9. 部署目录总览

```
C:\space-aiagent-v1\                    ← 项目根目录（PROJECT_ROOT）
├── src\
│   └── space_aiagent\                  ← Python 源码
│       ├── main.py                     ← 入口
│       ├── api\                        ← API + WebSocket
│       ├── agents\                     ← Agent 逻辑
│       ├── prompts\                    ← 提示词（打包在包内）
│       ├── tools\                      ← 工具组管理
│       ├── bridge\                     ← 远程工具桥接
│       ├── models\                     ← 数据模型
│       ├── middleware\                 ← Agent 中间件
│       └── infrastructure\             ← 配置、数据库、日志
├── config\                             ← 外部配置（可独立修改）
│   ├── application.yaml                ← 基础配置
│   ├── dev.yaml / prod.yaml            ← 环境覆盖
│   ├── subagents.yaml                  ← 子 Agent 配置
│   └── knowledge\
│       └── AGENTS.md                   ← 领域知识
├── data\                               ← 运行时数据（自动创建）
│   └── space_aiagent.db                ← SQLite 数据库
├── logs\                               ← 日志（自动创建）
├── .env                                ← 敏感配置（API Key）
├── pyproject.toml                      ← 项目定义
├── requirements.txt                    ← 依赖清单
├── run.bat                             ← 手动启动脚本
└── service.bat                         ← 服务模式启动脚本
```
