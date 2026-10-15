---
name: desktop-hello
description: 在当前 Windows 用户的桌面创建 hello.txt，内容为 hello。适用于测试技能脚本执行和文件写入。
---

执行 `scripts/create_hello.py`，无需参数或第三方依赖。

脚本读取 Windows 配置的桌面路径，在桌面写入 UTF-8 文本文件 `hello.txt`，内容精确为 `hello`（无换行）。同名文件已存在时覆盖其内容。成功后输出文件的绝对路径。

通过执行工具调用时，使用技能名 `desktop-hello`、资源路径 `scripts/create_hello.py`、参数 `[]`。

此技能应通过 Windows 本地执行器运行，创建的是执行进程所属用户的桌面文件；Linux Docker 容器不适用。
