# -*- coding: utf-8 -*-
"""
push_to_both.py  —  外网（个人电脑）前置脚本

背景：
    - 公司内网登录不了团队 GitHub，只能下 zip；且团队仓库内网也访问不到。
    - 所以在外网把同一份代码推到两个仓库：
        团队仓（干净的代码历史） + 个人仓（代码 + 本次改动清单 changed_files.txt）
    - 内网只下个人仓的 zip，里面自带 changed_files.txt，配合 sync_code.py 同步改动。

关键设计（避免团队仓被污染）：
    本地分支始终和团队仓保持一致，只有干净代码历史。
    changed_files.txt 作为一个「临时提交」强推到个人仓顶端，推完本地立刻回退掉。
    => 团队仓永远干净；个人仓顶端永远是「代码 + 最新清单」。

两个子命令：
    setup  —— 给当前本地仓配好 team / personal 两个 remote（只需跑一次）
        python push_to_both.py setup

    push   —— 日常一键双推（核心）
        python push_to_both.py push --range HEAD~1..HEAD -m "feat: 改了意图识别"

    --range 是「本次要同步到内网的改动范围」，你自己决定。例如：
        最近 1 个提交：  HEAD~1..HEAD
        最近 3 个提交：  HEAD~3..HEAD
        某两个提交之间： abc1234..def5678
"""

import argparse
import subprocess
import sys

# ============ 默认配置（你的两个仓库已填好） ============
TEAM_REMOTE = "team"            # 团队仓 remote 名
TEAM_URL = "git@github.com:cangjie-ai/ark-agentic.git"

PERSONAL_REMOTE = "personal"    # 个人仓 remote 名
PERSONAL_URL = "git@github.com:YU-JI-KUI/ark-agentic.git"

MANIFEST = "changed_files.txt"  # 改动清单文件名（会进个人仓 zip 根目录）
# =======================================================


def run(cmd, capture=False, check=True):
    """跑一条命令。

    给 Java 背景的说明：subprocess.run 类似 Java 的 ProcessBuilder。
    cmd 传列表（不是字符串），避免空格/特殊字符被 shell 错误拆分。
    capture=True 时把 stdout 收回来当返回值。
    """
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        text=True,                              # 用文本模式，输出是 str 不是 bytes
        capture_output=capture,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr)
        sys.exit(f"[错误] 命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")
    return result.stdout if capture else None


def git_output(args):
    """跑 git 并返回标准输出（去掉首尾空白）。"""
    return run(["git"] + args, capture=True).strip()


def ensure_in_git_repo():
    out = run(["git", "rev-parse", "--is-inside-work-tree"], capture=True, check=False)
    if not out or out.strip() != "true":
        sys.exit("[错误] 当前目录不是 git 仓库，请在你的项目目录下运行。")


def remote_exists(name):
    """判断某个 remote 是否已存在。"""
    remotes = git_output(["remote"]).splitlines()
    return name in remotes


def add_or_update_remote(name, url):
    """remote 不存在就 add，存在就把 url 更新成最新的。"""
    if remote_exists(name):
        print(f"  remote '{name}' 已存在，更新 URL")
        run(["git", "remote", "set-url", name, url])
    else:
        print(f"  添加 remote '{name}'")
        run(["git", "remote", "add", name, url])


# ---------------- setup 子命令 ----------------
def cmd_setup(args):
    ensure_in_git_repo()
    print("配置双 remote：")
    add_or_update_remote(TEAM_REMOTE, args.team_url)
    add_or_update_remote(PERSONAL_REMOTE, args.personal_url)
    print("\n当前 remote 列表：")
    print(git_output(["remote", "-v"]))
    print("\n配置完成。以后日常用： python push_to_both.py push --range HEAD~1..HEAD")


# ---------------- push 子命令 ----------------
def cmd_push(args):
    ensure_in_git_repo()

    if not remote_exists(TEAM_REMOTE) or not remote_exists(PERSONAL_REMOTE):
        sys.exit(f"[错误] remote 没配好，请先跑： python push_to_both.py setup")

    branch = git_output(["rev-parse", "--abbrev-ref", "HEAD"])
    print(f"当前分支：{branch}\n")

    # 1) 如果有未提交改动且给了 -m，就先把代码提交成一个干净提交
    status = git_output(["status", "--porcelain"])
    if status:
        if not args.message:
            sys.exit("[错误] 有未提交改动，请用 -m \"提交说明\" 让脚本提交，或先自己 git commit。")
        print("步骤 1/5：提交代码")
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", args.message])
    else:
        print("步骤 1/5：工作区干净，跳过提交（假设代码已提交）")

    code_tip = git_output(["rev-parse", "HEAD"])  # 记住代码提交点，最后要回退到这里

    # 2) 把干净的代码历史推到团队仓
    print("\n步骤 2/5：推送到团队仓")
    run(["git", "push", TEAM_REMOTE, branch])

    # 3) 按你给的范围生成改动清单（区分「复制」和「删除」）
    #    复制：--diff-filter=ACMRT = 新增/复制/修改/重命名/类型变更 的文件
    #    删除：--diff-filter=D     = 被删掉的文件
    print(f"\n步骤 3/5：生成改动清单（范围 {args.range}）")
    diff_copy = git_output(["diff", "--name-only", "--diff-filter=ACMRT", args.range])
    diff_del = git_output(["diff", "--name-only", "--diff-filter=D", args.range])
    copy_files = [f for f in diff_copy.splitlines() if f.strip() and f.strip() != MANIFEST]
    del_files = [f for f in diff_del.splitlines() if f.strip() and f.strip() != MANIFEST]
    if not copy_files and not del_files:
        sys.exit(f"[错误] 范围 {args.range} 内没有改动文件，检查 --range 是否写对。")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write("# 本次改动清单，由 push_to_both.py 自动生成\n")
        f.write("# 普通行 = 复制到内网；[DEL] 开头 = 在内网删除该文件\n")
        for rel in copy_files:
            f.write(rel + "\n")
        for rel in del_files:
            f.write("[DEL] " + rel + "\n")
    print(f"  复制 {len(copy_files)} 个、删除 {len(del_files)} 个，写入 {MANIFEST}：")
    for rel in copy_files:
        print(f"    [复制] {rel}")
    for rel in del_files:
        print(f"    [删除] {rel}")

    # 4) 把清单作为「临时提交」放到代码提交之上，强推到个人仓
    print("\n步骤 4/5：清单临时提交并强推到个人仓")
    run(["git", "add", MANIFEST])
    run(["git", "commit", "-m", "sync: update changed_files manifest"])
    # +branch 等价于 --force 推这个分支：个人仓每轮都被重写成「代码 + 最新清单」
    run(["git", "push", PERSONAL_REMOTE, f"+{branch}"])

    # 5) 本地回退掉清单提交，让本地分支重新等于团队仓（保持干净）
    print("\n步骤 5/5：本地回退临时提交，保持与团队仓一致")
    run(["git", "reset", "--hard", code_tip])

    print("\n" + "=" * 60)
    print("完成！")
    print(f"  团队仓 {TEAM_REMOTE}    : 干净代码历史")
    print(f"  个人仓 {PERSONAL_REMOTE}: 代码 + {MANIFEST}（内网下这个仓的 zip）")
    print(f"  本地分支已回到 {code_tip[:8]}，与团队仓一致")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="外网双推前置脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="配置 team / personal 两个 remote")
    p_setup.add_argument("--team-url", default=TEAM_URL)
    p_setup.add_argument("--personal-url", default=PERSONAL_URL)
    p_setup.set_defaults(func=cmd_setup)

    p_push = sub.add_parser("push", help="一键双推")
    p_push.add_argument("--range", default="HEAD~1..HEAD",
                        help="本次同步到内网的改动范围，默认最近一个提交 HEAD~1..HEAD")
    p_push.add_argument("-m", "--message", default=None,
                        help="若有未提交改动，用这个说明提交代码")
    p_push.set_defaults(func=cmd_push)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
