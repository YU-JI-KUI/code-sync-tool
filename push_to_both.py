# -*- coding: utf-8 -*-
"""
push_to_both.py  —  外网（个人电脑）前置脚本

背景：
    - 公司内网登录不了团队 GitHub，只能下 zip；且团队仓库内网也访问不到。
    - 我在外网开发时，代码会先正常进团队仓 master（PR merge 或 push）。
    - 然后用这个脚本把「团队仓 master 的最新代码」+「本次要同步的 commit 解析出的 changed_files 清单」
      推到个人仓，内网下个人仓的 zip，配合 sync_code.py 同步改动。

关键设计（避免团队仓被污染、个人仓干净）：
    本地 master 强制和团队仓 master 对齐 —— 不在本地做任何提交。
    个人仓的内容 = 团队仓 master 的代码 + 一个临时清单提交（强推、每轮重写）。
    => 团队仓全程不动；个人仓顶端永远是「最新 master 代码 + 本次清单」。

两个子命令：
    setup —— 给当前本地仓配好 team / personal 两个 remote（只跑一次）
        python push_to_both.py setup

    sync  —— 日常一键同步（核心）
        python push_to_both.py sync --commits <sha1> [<sha2> ...]

    --commits 可以是不连续的多个 commit（团队仓 master 上已经存在的提交），
    顺序无所谓：
        python push_to_both.py sync --commits <sha1> <sha2> <sha3>

    动作的最终判定以【team/master 当前状态】为准（master 即 SSOT）：
        - commit 列表收集到的所有"被触碰的"文件路径作为候选
        - 候选里在 team/master 上仍存在 → 复制
        - 候选里在 team/master 上不存在 → 删除
    这样无论中间夹了多少 rename / revert，结果都和 master 一致。

    脚本会：
        1) fetch team，把本地 master reset --hard 到 team/master（要求工作区干净）
        2) 对每个 commit 跑 git show 收集所有被触碰的文件路径
        3) 拿这些路径去 team/master 文件树里查存在性 → 决定复制 / 删除
        4) 写 changed_files.txt，临时提交后强推到 personal/master
        5) 本地 reset 回 team/master，保持干净
"""

import argparse
import subprocess
import sys

# ============ 默认配置（你的两个仓库已填好） ============
TEAM_REMOTE = "team"            # 团队仓 remote 名
TEAM_URL = "git@github.com:cangjie-ai/ark-agentic.git"

PERSONAL_REMOTE = "personal"    # 个人仓 remote 名
PERSONAL_URL = "git@github.com:YU-JI-KUI/ark-agentic.git"

DEFAULT_BRANCH = "master"       # 团队仓 / 个人仓的目标分支
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
        text=True,
        capture_output=capture,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr)
        sys.exit(f"[错误] 命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")
    return result.stdout if capture else None


def git_output(args):
    """跑 git 并返回标准输出（去掉首尾空白）。

    `-c core.quotepath=false` 让 git 不要对非 ASCII 路径做八进制转义
    （否则中文文件名会输出成 "\346\270\262..." 这种字符串，传给下游脚本会找不到文件）。
    """
    return run(["git", "-c", "core.quotepath=false"] + args, capture=True).strip()


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


def resolve_commit(sha):
    """把用户传的 commit 短 SHA 解析成完整 SHA。解析失败直接退出。"""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"[错误] commit 不存在或无法解析：{sha}\n{result.stderr.strip()}")
    return result.stdout.strip()


def commit_touched_files(full_sha):
    """跑 git show 拿单个 commit 触碰过的所有文件路径（新+旧），不区分动作。

    --no-renames       ：关闭 rename 检测，让 rename 拆成 "删旧路径 + 加新路径"
                         两条记录，旧路径才能进入候选集
    -m --first-parent  ：merge commit 也能正确展开成相对第一父提交的 diff
    """
    lines = git_output([
        "show", "--name-only", "--pretty=format:",
        "--no-renames", "-m", "--first-parent", full_sha,
    ]).splitlines()
    return [f.strip() for f in lines if f.strip() and f.strip() != MANIFEST]


# ---------------- setup 子命令 ----------------
def cmd_setup(args):
    ensure_in_git_repo()
    print("配置双 remote：")
    add_or_update_remote(TEAM_REMOTE, args.team_url)
    add_or_update_remote(PERSONAL_REMOTE, args.personal_url)
    print("\n当前 remote 列表：")
    print(git_output(["remote", "-v"]))
    print("\n配置完成。以后日常用： python push_to_both.py sync --commits <sha1> [<sha2> ...]")


# ---------------- sync 子命令 ----------------
def cmd_sync(args):
    ensure_in_git_repo()

    if not remote_exists(TEAM_REMOTE) or not remote_exists(PERSONAL_REMOTE):
        sys.exit("[错误] remote 没配好，请先跑： python push_to_both.py setup")

    # 工作区必须干净 —— 新流程不在本地做任何提交
    status = git_output(["status", "--porcelain"])
    if status:
        sys.exit(
            "[错误] 工作区有未提交改动。新流程假设代码已经在团队仓 master 上，\n"
            "       本地不应有任何未提交变化。请自行处理（commit / stash / 丢弃）后再跑。"
        )

    branch = args.branch

    # 1) 拉团队仓最新 master，本地强制对齐
    print(f"步骤 1/5：拉团队仓最新 {branch} 并强制对齐本地")
    run(["git", "fetch", TEAM_REMOTE, branch])
    run(["git", "checkout", branch])
    run(["git", "reset", "--hard", f"{TEAM_REMOTE}/{branch}"])

    code_tip = git_output(["rev-parse", "HEAD"])
    print(f"  本地 {branch} 已对齐到 {code_tip[:8]}")

    # 2) 解析用户指定的若干 commit（可以不连续，顺序无所谓）
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
                f"[错误] commit {sha} ({full[:8]}) 不在团队仓 {branch} 历史里。\n"
                f"       请确认这个提交已经合入团队仓 {branch}。"
            )
        subject = git_output(["log", "-1", "--pretty=format:%s", full])
        resolved.append((full, subject))
        print(f"    {full[:8]}  {subject}")

    # 3) 收集候选文件 → 用 team/master 真实文件树定动作
    print("\n步骤 3/5：以 team/master 为准定动作（存在=复制 / 不存在=删除）")
    candidates = {}  # path -> None，dict 保插入顺序便于稳定输出
    for full, _ in resolved:
        for f in commit_touched_files(full):
            candidates.setdefault(f, None)

    if not candidates:
        sys.exit("[错误] 指定的 commit 没有触碰任何文件，请检查 --commits 是否写对。")

    master_files = set(git_output(
        ["ls-tree", "-r", "--name-only", f"{TEAM_REMOTE}/{branch}"]
    ).splitlines())

    copy_files = [f for f in candidates if f in master_files]
    del_files = [f for f in candidates if f not in master_files]

    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write("# 本次改动清单，由 push_to_both.py 自动生成\n")
        f.write("# 普通行 = 复制到内网；[DEL] 开头 = 在内网删除该文件\n")
        f.write(f"# 基于团队仓 {branch} @ {code_tip[:8]}\n")
        f.write(f"# 涉及 commit（动作以 team/master 为准：存在=复制、不存在=删除）:\n")
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

    # 4) 把清单作为「临时提交」放到 master 顶端，强推到个人仓
    print("\n步骤 4/5：清单临时提交并强推到个人仓")
    run(["git", "add", MANIFEST])
    run(["git", "commit", "-m", "sync: update changed_files manifest"])
    run(["git", "push", PERSONAL_REMOTE, f"+{branch}"])

    # 5) 本地回退掉清单提交，让本地 master 重新等于团队仓 master
    print(f"\n步骤 5/5：本地回退临时提交，保持与团队仓 {branch} 一致")
    run(["git", "reset", "--hard", code_tip])

    print("\n" + "=" * 60)
    print("完成！")
    print(f"  团队仓 {TEAM_REMOTE}/{branch}    : 未动")
    print(f"  个人仓 {PERSONAL_REMOTE}/{branch}: 团队仓 {code_tip[:8]} 代码 + {MANIFEST}")
    print(f"  本地 {branch} 已回到 {code_tip[:8]}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="外网双推前置脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="配置 team / personal 两个 remote")
    p_setup.add_argument("--team-url", default=TEAM_URL)
    p_setup.add_argument("--personal-url", default=PERSONAL_URL)
    p_setup.set_defaults(func=cmd_setup)

    p_sync = sub.add_parser("sync", help="拉团队仓最新代码 + 指定 commit 的改动清单，推个人仓")
    p_sync.add_argument(
        "--commits", nargs="+", required=True, metavar="SHA",
        help="本次同步涉及的 commit（可多个、不连续），均需已合入团队仓 master",
    )
    p_sync.add_argument("--branch", default=DEFAULT_BRANCH,
                        help=f"目标分支，默认 {DEFAULT_BRANCH}")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
