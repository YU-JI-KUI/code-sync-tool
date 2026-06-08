# code-sync-tool

外网写代码、内网部署时的代码同步工具。解决「内网独有文件不能外带、内网无法访问团队仓」的问题。

## 两个脚本

- `push_to_both.py`：**外网（个人电脑）** 用。拉团队仓 master 最新代码，按你指定的若干 commit 生成 changed_files 清单，推到个人仓。
- `sync_code.py`：**内网** 用。下个人仓 zip，按清单把改动文件覆盖/删除到内网项目，内网独有文件不动。

详细用法见 [`使用说明.md`](./使用说明.md)。

## 工作模型

- **团队仓 master 是 SSOT**——你在外网开发时代码先正常进团队仓 master（PR merge / 直接 push）
- 跑 `push_to_both.py sync` 时，脚本本地强制对齐到 `team/master`，**不在本地做任何代码改动**
- 清单的"复制 vs 删除"动作**只看 team/master 当前状态**：master 上有 = 复制；master 上没有 = 删除。这样不管中间 commit 有 rename / revert / 改名都不会算错。

## 快速开始

外网，第一次：

```cmd
python push_to_both.py setup
```

外网，日常（可以传一个或多个 commit SHA，顺序无所谓，所有 commit 必须已在团队仓 master 上）：

```cmd
python push_to_both.py sync --commits abc1234 def5678 9012345
```

内网：

```cmd
python sync_code.py --zip ark-agentic-master.zip --project D:\work\ark-agentic --dry-run
python sync_code.py --zip ark-agentic-master.zip --project D:\work\ark-agentic
```

纯 Python 标准库，无需安装依赖；命令均为 Windows CMD 兼容。
