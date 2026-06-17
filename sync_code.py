# -*- coding: utf-8 -*-
"""
sync_code.py  —  把 GitHub 下载的 zip 包里的改动文件，复制到内网项目对应路径

用途：
    你在外网写完代码 -> push 到 GitHub -> 内网下载 zip。
    这个脚本读取一份「改动文件清单」（txt），把清单里列到的文件
    从 zip 解压内容复制到内网项目的相同相对路径下。
    内网项目里独有的文件不会被动到。

三个输入：
    1. ZIP_PATH      —— 从 GitHub 下载的 zip 包路径
    2. PROJECT_ROOT  —— 内网项目根目录
    3. LIST_PATH     —— 改动文件清单 txt（每行一个相对路径）

用法（Windows CMD）：
    方式一：直接改下面三个变量，然后双击或运行   python sync_code.py
    方式二：命令行传参覆盖
        python sync_code.py --zip code.zip --project D:\work\myproj --list changed_files.txt
        python sync_code.py ... --dry-run     （只预览不真正复制，建议第一次先跑这个）

    --zip 支持通配符：浏览器下第二份同名 zip 会加 (1)(2) 后缀，用通配符可一劳永逸
        python sync_code.py --zip "C:\Downloads\ark-agentic-master*.zip" --project D:\work\ark-agentic
        （多个匹配时按修改时间自动选最新的那个，命令永远不用改）

策略：同名文件 **直接覆盖**（按你的选择）。
"""

import argparse
import glob
import os
import shutil
import sys
import tempfile
import zipfile

# ============ 默认配置：可以直接改这里，省得每次敲命令行 ============
ZIP_PATH = r"code.zip"                  # GitHub 下载的 zip
PROJECT_ROOT = r"D:\work\my_project"    # 内网项目根目录
LIST_PATH = r"changed_files.txt"        # 改动文件清单
# ===================================================================


def parse_args():
    """解析命令行参数。命令行传了就用命令行的，没传就用上面的默认值。

    说明（给 Java 背景看）：argparse 是 Python 标准库里做命令行解析的，
    类似 Java 里自己解析 args[] 但省事很多。default=XXX 就是缺省值。
    """
    p = argparse.ArgumentParser(description="把 GitHub zip 里的改动文件同步到内网项目")
    p.add_argument("--zip", dest="zip_path", default=ZIP_PATH,
                   help=r"GitHub 下载的 zip 路径，支持通配符。"
                        r"例如 --zip C:\Downloads\ark-agentic-master*.zip 会自动选最新的那个，"
                        r"不用关心浏览器加的 (1)(2) 后缀。")
    p.add_argument("--project", dest="project_root", default=PROJECT_ROOT, help="内网项目根目录")
    # 注意 default=None：不传 --list 时，自动用 zip 里自带的 changed_files.txt
    p.add_argument("--list", dest="list_path", default=None,
                   help="改动文件清单 txt；不指定就自动读 zip 里自带的 changed_files.txt")
    p.add_argument("--dry-run", action="store_true", help="只预览要复制哪些文件，不实际复制")
    return p.parse_args()


def read_file_list(list_path):
    """读取改动清单 txt，返回相对路径列表。

    规则：
      - 每行一个相对路径，比如  src/utils/intent.py
      - 空行跳过
      - 以 # 开头的行当注释跳过
      - 反斜杠 \\ 统一转成正斜杠 /，避免 Windows 路径在 zip 里匹配不上
      - [DEL] 开头的行表示「删除」，其余是「复制」

    返回 (action, rel_path) 的列表，action 是 "copy" 或 "delete"。
    """
    if not os.path.isfile(list_path):
        sys.exit(f"[错误] 找不到改动清单文件：{list_path}")

    items = []
    # 用 utf-8-sig 能顺手吃掉 Windows 记事本存的 BOM 头，避免第一行路径带个看不见的字符
    with open(list_path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[DEL]"):
                # 去掉 [DEL] 前缀，剩下的是要删除的相对路径
                rel = line[len("[DEL]"):].strip().replace("\\", "/")
                if rel:
                    items.append(("delete", rel))
            else:
                items.append(("copy", line.replace("\\", "/")))
    return items


def print_manifest_header(list_path):
    """打印清单文件开头的 # 注释（push_to_both.py 写入的元信息：基于哪个 commit 等）。

    遇到第一行非注释就停。让你在内网执行时一眼看出这次同步基于什么。
    """
    print("--- 清单元信息（来自外网生成时的快照）---")
    with open(list_path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.rstrip()
            if not line.startswith("#"):
                break
            print(f"  {line}")
    print("-" * 42)


def extract_zip(zip_path, work_dir):
    """把 zip 解压到 work_dir，返回真正的代码根目录。

    GitHub 下载的 zip 解压后外面会套一层文件夹，比如 myrepo-main/，
    真正的代码在那一层里面。这里自动识别并返回那个内层目录。
    """
    if not os.path.isfile(zip_path):
        sys.exit(f"[错误] 找不到 zip 包：{zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(work_dir)

    # 解压后看 work_dir 下有什么。如果只有一个文件夹，说明就是 GitHub 套的那层，进去。
    entries = [e for e in os.listdir(work_dir) if not e.startswith("__MACOSX")]
    if len(entries) == 1 and os.path.isdir(os.path.join(work_dir, entries[0])):
        return os.path.join(work_dir, entries[0])
    return work_dir


def sync(code_root, project_root, items, dry_run):
    """核心：按 items 列表，复制或删除内网项目里的文件。

    items 是 (action, rel_path) 列表，action 为 "copy" 或 "delete"。
    返回统计字典。删除时文件不存在就跳过，绝不报错（这是预期行为）。
    """
    stats = {
        "added": 0,        # 新增（内网原本没有的复制）
        "overwritten": 0,  # 覆盖（内网原本有的复制）
        "deleted": 0,      # 真删除（内网原本存在）
        "skipped_del": 0,  # 跳过删除（内网原本就不存在）
        "missing": [],     # 清单要求复制但 zip 里没有的文件
    }

    for action, rel in items:
        dst = os.path.join(project_root, rel)

        # ---- 删除 ----
        if action == "delete":
            if not os.path.isfile(dst):
                # 内网本来就没有这个文件，跳过即可（这是正常情况，不算错）
                print(f"  [跳过删除] 内网原本不存在： {rel}")
                stats["skipped_del"] += 1
                continue
            size_kb = os.path.getsize(dst) / 1024
            if dry_run:
                print(f"  [预览-删除] {rel}  ({size_kb:.1f} KB)")
                stats["deleted"] += 1
                continue
            os.remove(dst)
            print(f"  [删除] {rel}  ({size_kb:.1f} KB)")
            stats["deleted"] += 1
            continue

        # ---- 复制 ----
        src = os.path.join(code_root, rel)
        if not os.path.isfile(src):
            stats["missing"].append(rel)
            print(f"  [缺失] zip 里没有： {rel}")
            continue

        if os.path.exists(dst):
            verb, key = "覆盖", "overwritten"
        else:
            verb, key = "新增", "added"
        size_kb = os.path.getsize(src) / 1024
        if dry_run:
            print(f"  [预览-{verb}] {rel}  ({size_kb:.1f} KB)")
            stats[key] += 1
            continue

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  [{verb}] {rel}  ({size_kb:.1f} KB)")
        stats[key] += 1

    return stats


def resolve_zip_path(pattern):
    """把用户传的 --zip 解析成真实文件路径。

    支持两种形式：
      1) 精确路径：原样返回（保持向后兼容）
      2) 通配符（含 * 或 ?）：匹配所有候选，按修改时间挑最新的那个

    这是为了解决"浏览器下第二份同名 zip 会被加 (1)(2) 后缀"导致命令必须改名的痛点。
    用户写 ark-agentic-master*.zip，永远拿到最新下载的那一份。
    """
    if "*" not in pattern and "?" not in pattern:
        return pattern

    matches = glob.glob(pattern)
    if not matches:
        sys.exit(f"[错误] 通配符 {pattern} 没匹配到任何文件。\n"
                 f"  检查路径是否写对、zip 是否真的在那个目录下。")

    matches.sort(key=os.path.getmtime, reverse=True)
    chosen = matches[0]
    if len(matches) > 1:
        print(f"通配符 {pattern} 匹配到 {len(matches)} 个文件，选最新的：")
        for m in matches[:5]:
            mark = " <-- 用这个" if m == chosen else ""
            print(f"    {m}{mark}")
        if len(matches) > 5:
            print(f"    ... 另有 {len(matches) - 5} 个")
    return chosen


def main():
    args = parse_args()
    args.zip_path = resolve_zip_path(args.zip_path)

    print("=" * 60)
    print(f"zip 包       : {args.zip_path}")
    print(f"内网项目根目录: {args.project_root}")
    print(f"改动清单     : {args.list_path or '(自动读 zip 里自带的 changed_files.txt)'}")
    if args.dry_run:
        print(">>> DRY-RUN 预览模式：不会真正复制任何文件 <<<")
    print("=" * 60)

    if not os.path.isdir(args.project_root):
        sys.exit(f"[错误] 内网项目根目录不存在：{args.project_root}")

    # 解压到系统临时目录，跑完自动删掉。mkdtemp 每次生成唯一目录，不会和上次残留撞名。
    work_dir = tempfile.mkdtemp(prefix="sync_extract_")

    try:
        code_root = extract_zip(args.zip_path, work_dir)

        # 没指定 --list 就用 zip 里自带的清单（push_to_both.py 已经把它放进个人仓 zip 根目录）
        list_path = args.list_path or os.path.join(code_root, "changed_files.txt")
        if not os.path.isfile(list_path):
            sys.exit(f"[错误] 找不到改动清单：{list_path}\n"
                     f"  要么用 --list 指定，要么确认个人仓 zip 根目录里有 changed_files.txt。")
        items = read_file_list(list_path)
        if not items:
            sys.exit("[错误] 改动清单里没有任何有效路径，检查一下 txt 内容。")

        print_manifest_header(list_path)
        n_copy = sum(1 for a, _ in items if a == "copy")
        n_del = sum(1 for a, _ in items if a == "delete")
        print(f"\n清单共 {len(items)} 项（复制 {n_copy}、删除 {n_del}）。开始处理：\n")

        stats = sync(code_root, args.project_root, items, args.dry_run)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    verb = "将" if args.dry_run else "已"
    print(f"{verb}新增 {stats['added']} 个、{verb}覆盖 {stats['overwritten']} 个、"
          f"{verb}删除 {stats['deleted']} 个。")
    print(f"跳过删除（内网原本不存在）{stats['skipped_del']} 个、"
          f"缺失（zip 里没有）{len(stats['missing'])} 个。")
    if stats["missing"]:
        print("\n缺失列表（zip 里没找到，请检查清单或重新下载 zip）：")
        for m in stats["missing"]:
            print(f"  - {m}")
    if args.dry_run:
        print("\n这是预览。确认无误后，去掉 --dry-run 再跑一次即可真正复制。")
    print("=" * 60)


if __name__ == "__main__":
    main()
