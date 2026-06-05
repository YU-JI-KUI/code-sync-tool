# code-sync-tool

外网写代码、内网部署时的代码同步工具。解决「内网独有文件不能外带、内网无法访问团队仓」的问题。

两个脚本：

- `push_to_both.py`：**外网（个人电脑）** 用。一份代码双推到团队仓 + 个人仓，自动把本次改动清单（含删除）带进个人仓。
- `sync_code.py`：**内网** 用。下个人仓 zip，按清单把改动文件覆盖/删除到内网项目，内网独有文件不动。

详细用法见 [`使用说明.md`](./使用说明.md)。

## 快速开始

外网，第一次：

```cmd
python push_to_both.py setup
```

外网，日常：

```cmd
python push_to_both.py push --range HEAD~1..HEAD -m "feat: 改了意图识别"
```

内网：

```cmd
python sync_code.py --zip ark-agentic-main.zip --project D:\work\ark-agentic --dry-run
python sync_code.py --zip ark-agentic-main.zip --project D:\work\ark-agentic
```

纯 Python 标准库，无需安装依赖；命令均为 Windows CMD 兼容。
