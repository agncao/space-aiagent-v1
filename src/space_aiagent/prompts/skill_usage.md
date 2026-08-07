## 技能（Skills）使用规则（必须遵守）

系统会根据当前任务与 Skill description 进行预路由，并在本提示词末尾注入
“已自动激活的 Skills”全文。存在该段时，必须严格执行其中流程，无需再次读取对应主 SKILL.md。

若运行环境仅提供“Available Skills”元数据而没有自动激活段，则使用兼容流程：

1. 比较用户意图与每个 Skill 的 description。
2. 命中后，在调用任何业务工具之前执行
   `read_file(file_path="<清单中的 SKILL.md 路径>", limit=1000)` 完整读取。
3. 严格遵守 Skill 的参数规则、工具顺序和失败处理，不得根据工具列表绕过 Skill。
4. 只有确认没有 Skill 匹配时才可直接调用未受 Skill 管理的业务工具。

不得加载无关 Skill；多个独立意图可以分别激活多个 Skill。
