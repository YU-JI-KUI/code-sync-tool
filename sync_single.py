# -*- coding: utf-8 -*-
"""
sync_single.py  —  单 remote 版同步脚本

适用场景：
    项目只有一个 GitHub 仓（个人仓），不存在团队仓 / 个人仓分离的需求。
    外网开发的代码就直接进这个仓的 master，内网下 zip 用 sync_code.py 同步。

和 push_to_both.py 的区别：
    push_to_both.py：team + personal 双 remote，团队仓干净、个人仓夹清单
    sync_single.py：只有一个 remote（默认 origin），清单作为常规 commit 进 master

工作模型（同 push_to_both.py 的核心思想）：
    - origin/master 是 SSOT
    - 本地 master 强制 reset 到 origin/master，要求工作区干净
    - 收集你指定 commit 触碰过的文件，以 origin/master 当前状态定动作：
        - 文件在 master 上仍存在 → 复制
        - 文件在 master 上不存在 → 删除
    - 清单作为一个普通 commit push 到 origin/master（不是强推、不重写历史）

用法：
    python sync_single.py sync --commits <sha1> [<sha2> ...]
    python sync_single.py sync --commits abc1234 def5678 --remote origin --branch master
"""

import argparse
import subprocess
import sys

DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "master"
MANIFEST = "changed_files.txt"


def run(cmd, capture=False, check=True):
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True, capture_output=capture, encoding="utf-8")
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr)
        sys.exit(f"[错误] 命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")
    return result.stdout if capture else None


def git_output(args):
    """跑 git 并返回标准输出。

    `-c core.quotepath=false` 让 git 不要对非 ASCII 路径做八进制转义
    （否则中文文件名会变成 "\346\270..." 形式，下游脚本找不到文件）。
    """
    return run(["git", "-c", "core.quotepath=false"] + args, capture=True).strip()


def ensure_in_git_repo():
    out = run(["git", "rev-parse", "--is-inside-work-tree"], capture=True, check=False)
    if not out or out.strip() != "true":
        sys.exit("[错误] 当前目录不是 git 仓库，请在你的项目目录下运行。")


def resolve_commit(sha):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        text=True, capture_output=True, encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"[错误] commit 不存在或无法解析：{sha}\n{result.stderr.strip()}")
    return result.stdout.strip()


def commit_touched_files(full_sha):
    """跑 git show 拿单个 commit 触碰过的所有文件路径（新+旧），不区分动作。

    --no-renames：关闭 rename 检测，让 rename 拆成"删旧路径 + 加新路径"两条记录。
    -m --first-parent：merge commit 也能正确展开。
    """
    lines = git_output([
        "show", "--name-only", "--pretty=format:",
        "--no-renames", "-m", "--first-parent", full_sha,
    ]).splitlines()
    return [f.strip() for f in lines if f.strip() and f.strip() != MANIFEST]


def cmd_sync(args):
    ensure_in_git_repo()

    remote = args.remote
    branch = args.branch

    # 工作区必须干净
    status = git_output(["status", "--porcelain"])
    if status:
        sys.exit(
            f"[错误] 工作区有未提交改动：\n{status}\n"
            "请先 commit / stash / 丢弃后再跑。"
        )

    # 1) fetch + 本地强制对齐
    print(f"步骤 1/5：拉 {remote}/{branch} 最新代码并强制对齐本地")
    run(["git", "fetch", remote, branch])
    run(["git", "checkout", branch])
    run(["git", "reset", "--hard", f"{remote}/{branch}"])

    code_tip = git_output(["rev-parse", "HEAD"])
    print(f"  本地 {branch} 已对齐到 {code_tip[:8]}")

    # 2) 解析指定 commit
    print(f"\n步骤 2/5：解析 {len(args.commits)} 个指定 commit")
    resolved = []
    for sha in args.commits:
        full = resolve_commit(sha)
        merge_base = subprocess.run(
            ["git", "merge-base", "--is-ancestor", full, code_tip],
            capture_output=True,
        )
        if merge_base.returncode != 0:
            sys.exit(
                f"[错误] commit {sha} ({full[:8]}) 不在 {remote}/{branch} 历史里。\n"
                f"       请确认这个提交已经合入 {remote}/{branch}。"
            )
        subject = git_output(["log", "-1", "--pretty=format:%s", full])
        resolved.append((full, subject))
        print(f"    {full[:8]}  {subject}")

    # 3) 收集候选文件 → 用 origin/master 真实文件树定动作
    print(f"\n步骤 3/5：以 {remote}/{branch} 为准定动作（存在=复制 / 不存在=删除）")
    candidates = {}
    for full, _ in resolved:
        for f in commit_touched_files(full):
            candidates.setdefault(f, None)

    if not candidates:
        sys.exit("[错误] 指定的 commit 没有触碰任何文件，请检查 --commits 是否写对。")

    master_files = set(git_output(
        ["ls-tree", "-r", "--name-only", f"{remote}/{branch}"]
    ).splitlines())

    copy_files = [f for f in candidates if f in master_files]
    del_files = [f for f in candidates if f not in master_files]

    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write("# 本次改动清单，由 sync_single.py 自动生成\n")
        f.write("# 普通行 = 复制到内网；[DEL] 开头 = 在内网删除该文件\n")
        f.write(f"# 基于 {remote}/{branch} @ {code_tip[:8]}\n")
        f.write(f"# 涉及 commit（动作以 {remote}/{branch} 为准：存在=复制、不存在=删除）:\n")
        for full, subject in resolved:
            f.write(f"#   {full[:8]} {subject}\n")
        for rel in copy_files:
            f.write(rel + "\n")
        for rel in del_files:
            f.write("[DEL] " + rel + "\n")
    print(f"  复制 {len(copy_files)} 个、删除 {len(del_files)} 个，写入 {MANIFEST}：")
    for rel in copy_files:
        print(f"    [复制] {rel}")
    for rel in del_files:
        print(f"    [删除] {rel}")

    # 4) 把清单作为常规 commit 推到 master（不是强推）
    print(f"\n步骤 4/5：提交清单并推送到 {remote}/{branch}")
    run(["git", "add", MANIFEST])
    commit_msg = f"sync: changed_files for {len(resolved)} commit(s)"
    run(["git", "commit", "-m", commit_msg])
    run(["git", "push", remote, branch])

    new_tip = git_output(["rev-parse", "HEAD"])
    print(f"\n步骤 5/5：完成。{branch} 顶端已是清单 commit {new_tip[:8]}")

    print("\n" + "=" * 60)
    print("完成！")
    print(f"  {remote}/{branch} 顶端: {new_tip[:8]}（清单 commit）")
    print(f"  代码 commit:           {code_tip[:8]}（清单的父提交）")
    print(f"  内网下 {remote} 的 zip 即可，{MANIFEST} 已在仓库根目录")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="单 remote 版同步脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="生成 changed_files 清单并推到 master")
    p_sync.add_argument(
        "--commits", nargs="+", required=True, metavar="SHA",
        help="本次同步涉及的 commit（可多个、不连续），均需已合入 master",
    )
    p_sync.add_argument("--remote", default=DEFAULT_REMOTE,
                        help=f"远端名称，默认 {DEFAULT_REMOTE}")
    p_sync.add_argument("--branch", default=DEFAULT_BRANCH,
                        help=f"目标分支，默认 {DEFAULT_BRANCH}")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
