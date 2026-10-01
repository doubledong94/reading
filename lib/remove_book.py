#!/usr/bin/env python3
"""remove_book - 从工作区删除指定书籍, 并清理其书目登记。

删除动作 (缺一不可):
    1. 删除 books/<slug>/ 整个目录 (source.pdf / md / index.json / AGENTS.md)
    2. 从工作区 catalog.md 移除该书所在行
    3. 从工作区 AGENTS.md 的「本区书目路由」表移除该书所在行

用法:
    python3 lib/remove_book.py <workspace> <slug> [<slug> ...] [-y] [--dry-run]

选项:
    -y/--yes    不交互确认 (脚本/AI 用)
    -n/--dry-run 只展示将删除的内容, 不做任何改动
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACES = os.path.join(REPO, "workspaces")

WS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def resolve_workspace(name: str) -> str:
    """校验并返回工作区目录的绝对路径。"""
    if not WS_RE.match(name):
        raise SystemExit(
            f"错误: 非法 workspace 名 '{name}' "
            f"(只允许小写字母/数字/连字符, 且以字母或数字开头)")
    ws_dir = os.path.join(WORKSPACES, name)
    if not os.path.isdir(ws_dir):
        available = ", ".join(sorted(
            d for d in os.listdir(WORKSPACES)
            if os.path.isdir(os.path.join(WORKSPACES, d)))) if os.path.isdir(WORKSPACES) else ""
        raise SystemExit(
            f"错误: 工作区不存在: {ws_dir}" +
            (f"; 可用: {available}" if available else ""))
    return ws_dir


def _row_pattern(slug: str) -> re.Pattern:
    """匹配登记表中含 `slug` 单元格的表格行 (catalog 或 AGENTS 路由表通用)。"""
    return re.compile(r"\|\s*`" + re.escape(slug) + r"`\s*\|")


def strip_rows(path: str, slugs: set[str], dry_run: bool) -> tuple[int, list[str]]:
    """删除登记文件中引用任一 slug 的表格行。返回 (删除行数, 命中的 slug)。"""
    if not os.path.isfile(path):
        return 0, []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    kept: list[str] = []
    removed = 0
    hit: set[str] = set()
    for ln in lines:
        matched = next((s for s in slugs
                        if ln.lstrip().startswith("|") and _row_pattern(s).search(ln)),
                       None)
        if matched is not None:
            removed += 1
            hit.add(matched)
        else:
            kept.append(ln)

    if removed and not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")
    return removed, sorted(hit)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="remove_book",
        description="从工作区删除指定书籍, 并清理 catalog.md / AGENTS.md 的登记。",
    )
    ap.add_argument("workspace", help="工作区名 (workspaces/<name>)")
    ap.add_argument("slugs", nargs="+", help="要删除的书籍 slug (可给多个)")
    ap.add_argument("-y", "--yes", action="store_true", help="不交互确认")
    ap.add_argument("-n", "--dry-run", action="store_true", help="只展示, 不实际删除")
    args = ap.parse_args(argv)

    ws_dir = resolve_workspace(args.workspace)
    books_dir = os.path.join(ws_dir, "books")

    targets = []
    missing = []
    for slug in args.slugs:
        book_dir = os.path.join(books_dir, slug)
        if os.path.isdir(book_dir):
            targets.append((slug, book_dir))
        else:
            missing.append(slug)
    if missing:
        available = ", ".join(sorted(
            d for d in os.listdir(books_dir)
            if os.path.isdir(os.path.join(books_dir, d)))) if os.path.isdir(books_dir) else ""
        raise SystemExit(
            f"错误: 书籍不存在: {', '.join(missing)}" +
            (f"; 该区可用: {available}" if available else ""))

    catalog = os.path.join(ws_dir, "catalog.md")
    ws_agents = os.path.join(ws_dir, "AGENTS.md")

    if not args.yes and not args.dry_run:
        print(f"将从工作区 '{args.workspace}' 删除 {len(targets)} 本书:")
        for slug, book_dir in targets:
            print(f"  - {os.path.relpath(book_dir, REPO)}")
        print("同时会清理 catalog.md 与 AGENTS.md 中的对应登记。")
        try:
            reply = input("确认? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("已取消, 未做任何改动。")
            return 1

    slug_set = {s for s, _ in targets}
    for _, book_dir in targets:
        if args.dry_run:
            print(f"[dry-run] 将删除 {os.path.relpath(book_dir, REPO)}")
        else:
            shutil.rmtree(book_dir)
            print(f"已删除 {os.path.relpath(book_dir, REPO)}")
    n_cat, _ = strip_rows(catalog, slug_set, args.dry_run)
    n_ag, _ = strip_rows(ws_agents, slug_set, args.dry_run)
    verb = "将清理" if args.dry_run else "已清理"
    print(f"{verb}登记: catalog.md {n_cat} 行, AGENTS.md {n_ag} 行")

    if not args.dry_run:
        remaining = [d for d in os.listdir(books_dir)
                     if os.path.isdir(os.path.join(books_dir, d))] \
            if os.path.isdir(books_dir) else []
        if not remaining:
            print(f"提示: 工作区 '{args.workspace}' 已无书籍, "
                  f"如需整体移除可删除 {os.path.relpath(ws_dir, REPO)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
