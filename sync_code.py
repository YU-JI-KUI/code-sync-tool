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

策略：同名文件 **直接覆盖**（按你的选择）。
"""

import argparse
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
    p.add_argument("--zip", dest="zip_path", default=ZIP_PATH, help="GitHub 下载的 zip 路径")
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
    返回 (复制数, 删除数, 缺失列表)。删除时文件不存在就跳过，绝不报错。
    """
    copied = 0
    deleted = 0
    missing = []

    for action, rel in items:
        dst = os.path.join(project_root, rel)

        # ---- 删除 ----
        if action == "delete":
            if not os.path.isfile(dst):
                # 内网本来就没有这个文件，跳过即可（你的要求：没有则跳过）
                print(f"  [跳过删除] 内网没有： {rel}")
                continue
            if dry_run:
                print(f"  [预览-删除] {rel}")
                deleted += 1
                continue
            os.remove(dst)
            print(f"  [删除] {rel}")
            deleted += 1
            continue

        # ---- 复制 ----
        src = os.path.join(code_root, rel)
        if not os.path.isfile(src):
            # zip 里没有这个文件——可能清单写错了
            missing.append(rel)
            print(f"  [缺失] zip 里没有： {rel}")
            continue

        verb = "覆盖" if os.path.exists(dst) else "新增"
        if dry_run:
            print(f"  [预览-{verb}] {rel}")
            copied += 1
            continue

        # 确保目标目录存在（内网项目里可能还没这个子目录）
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # copy2 会连同文件的修改时间等元信息一起拷过去，相当于完整复制
        shutil.copy2(src, dst)
        print(f"  [{verb}] {rel}")
        copied += 1

    return copied, deleted, missing


def main():
    args = parse_args()

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
        n_copy = sum(1 for a, _ in items if a == "copy")
        n_del = sum(1 for a, _ in items if a == "delete")
        print(f"清单共 {len(items)} 项（复制 {n_copy}、删除 {n_del}）。\n")

        copied, deleted, missing = sync(code_root, args.project_root, items, args.dry_run)
    finally:
        # finally 保证不管中间报不报错，临时目录都会被清掉（类似 Java 的 try-finally）
        shutil.rmtree(work_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    verb = "将" if args.dry_run else "已"
    print(f"{verb}复制 {copied} 个、{verb}删除 {deleted} 个。缺失 {len(missing)} 个。")
    if missing:
        print("缺失列表（zip 里没找到，请检查清单或文件名）：")
        for m in missing:
            print(f"  - {m}")
    if args.dry_run:
        print("\n这是预览。确认无误后，去掉 --dry-run 再跑一次即可真正复制。")
    print("=" * 60)


if __name__ == "__main__":
    main()
