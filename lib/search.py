#!/usr/bin/env python3
"""search - 跨书全文检索, 直接定位到 书 / 页 / 所属章节。

搜索范围 (按优先级三选一):
    1. -w/--workspace NAME  显式指定
    2. cwd 在 workspaces/<name>/ 下  -> 只搜该工作区
    3. cwd 在仓库根                    -> 跨全部工作区

用法:
    cd workspaces/csg && python3 ../../lib/search.py "PathExpander"
    python3 lib/search.py "MERGE" -w csg -n 5
    python3 lib/search.py "apoc\\.path" -r --json

选项:
    -w/--workspace  限定工作区 (缺省按 cwd 推断)
    -s/--slug       只搜指定书 (可重复)
    -n/--limit      每本书最多输出的命中页数 (默认 8, 0=不限)
    -r/--regex      关键词按正则解释
    --case-sensitive  区分大小写 (默认忽略)
    --json          输出 JSON (便于程序调用)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = os.path.join(REPO, "books")


def workspace_dirs() -> list[tuple[str, str]]:
    """[(工作区名, books 目录)] —— 只列含 books/ 的工作区。"""
    root = os.path.join(REPO, "workspaces")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        bd = os.path.join(root, name, "books")
        if os.path.isdir(bd):
            out.append((name, bd))
    return out


def current_workspace() -> str | None:
    """按当前目录推断工作区: cwd 在 workspaces/<name>/ 下 -> name。"""
    try:
        rel = os.path.relpath(os.getcwd(), REPO)
    except ValueError:
        return None
    parts = rel.split(os.sep)
    if parts[0] == "workspaces" and len(parts) > 1 and parts[1]:
        return parts[1]
    return None


def resolve_scope(ws_arg: str | None) -> list[tuple[str, str]]:
    """确定搜索范围: -w 指定 > cwd 所在工作区 > 全部工作区。"""
    dirs = workspace_dirs()
    if ws_arg:
        picked = [d for d in dirs if d[0] == ws_arg]
        if not picked:
            available = ", ".join(n for n, _ in dirs) or "(无)"
            raise SystemExit(f"错误: 未找到工作区 '{ws_arg}'; 可用: {available}")
        return picked
    cw = current_workspace()
    if cw:
        return [d for d in dirs if d[0] == cw]
    if dirs:
        return dirs                      # 仓库根: 跨工作区搜索
    if os.path.isdir(BOOKS):             # 兼容无 workspaces 的旧结构
        return [("", BOOKS)]
    raise SystemExit("错误: 未找到可搜索的 books/ (先运行 ./add_book.sh <workspace> <pdf>)")


def load_books(scope: list[tuple[str, str]], slugs: list[str]) -> list[dict]:
    out = []
    for ws, books_dir in scope:
        for name in sorted(os.listdir(books_dir)):
            if slugs and name not in slugs:
                continue
            idx_path = os.path.join(books_dir, name, "index.json")
            if not os.path.isfile(idx_path):
                continue
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
            idx["_ws"] = ws
            idx["_dir"] = books_dir
            out.append(idx)
    if slugs:
        missing = set(slugs) - {b["slug"] for b in out}
        if missing:
            available = ", ".join(b["slug"] for b in load_books(scope, []))
            raise SystemExit(f"错误: 未找到书籍 {', '.join(sorted(missing))}; 可用: {available}")
    if not out:
        raise SystemExit("错误: 范围内没有书籍 (先运行 ./add_book.sh <workspace> <pdf>)")
    return out


def chapter_of(index: dict, pdf_page: int) -> str:
    """该页所属章节: 书签顺序中最后一个起始页 <= 该页的章节。"""
    best = ""
    for c in index["chapters"]:
        if c["pdf_page"] <= pdf_page:
            best = c["title"]
        else:
            break
    return best


def search_book(index: dict, pattern: re.Pattern, limit: int) -> list[dict]:
    slug = index["slug"]
    md_dir = os.path.join(index["_dir"], slug, "md")
    if not os.path.isdir(md_dir):
        return []
    printed = {m["pdf"]: m["printed"] for m in index["page_map"]}
    hits = []
    for entry in sorted(os.listdir(md_dir)):
        if not (entry.startswith("page_") and entry.endswith(".md")):
            continue
        pdf_page = int(entry[5:9])
        path = os.path.join(md_dir, entry)
        try:
            f = open(path, encoding="utf-8")
        except OSError:
            continue
        with f:
            lineno = 0
            for lineno, line in enumerate(f, 1):
                m = pattern.search(line)
                if not m:
                    continue
                snippet = line.strip()
                if len(snippet) > 160:
                    start = max(0, m.start() - 60)
                    snippet = ("…" if start else "") + snippet[start:start + 160] + "…"
                hits.append({
                    "slug": slug,
                    "page": entry,
                    "pdf_page": pdf_page,
                    "printed": printed.get(pdf_page, ""),
                    "chapter": chapter_of(index, pdf_page),
                    "line": lineno,
                    "text": snippet,
                })
                break  # 每页只报第一处命中
        if limit and len([h for h in hits if h["page"] == entry]) and \
                len({h["page"] for h in hits}) >= limit:
            break
    if limit:
        seen, kept = set(), []
        for h in hits:
            if h["page"] in seen:
                continue
            seen.add(h["page"])
            kept.append(h)
        hits = kept[:limit]
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="search", description="跨书全文检索, 定位到 书/页/章节")
    ap.add_argument("keywords", nargs="+", help="检索词 (多个词=AND)")
    ap.add_argument("-w", "--workspace", default=None,
                    help="限定工作区 (缺省: cwd 所在工作区, 在仓库根则跨区)")
    ap.add_argument("-s", "--slug", action="append", default=[], help="只搜指定书 (可重复)")
    ap.add_argument("-n", "--limit", type=int, default=8, help="每本书最多命中页数 (默认 8, 0=不限)")
    ap.add_argument("-r", "--regex", action="store_true", help="按正则解释")
    ap.add_argument("--case-sensitive", action="store_true", help="区分大小写 (默认忽略)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    scope = resolve_scope(args.workspace)
    books = load_books(scope, args.slug)
    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        if args.regex:
            joined = "(?=.*%s)" % ")(?=.*".join(args.keywords)
            pattern = re.compile(joined, flags)
        else:
            pattern = re.compile("(?=.*" + ")(?=.*".join(
                re.escape(k) for k in args.keywords) + ")", flags)
    except re.error as e:
        raise SystemExit(f"错误: 正则无效: {e}")

    result = []
    for book in books:
        hits = search_book(book, pattern, args.limit)
        if hits:
            result.append({"book": book, "hits": hits})

    if args.json:
        slim = [{"workspace": r["book"]["_ws"], "slug": r["book"]["slug"],
                 "title": r["book"]["title"], "pages": r["book"]["pages"],
                 "chapters": len(r["book"]["chapters"]), "hits": r["hits"]}
                for r in result]
        print(json.dumps(slim, ensure_ascii=False, indent=1))
        return 0

    if not result:
        print("无命中")
        return 1

    total = sum(len(r["hits"]) for r in result)
    scope_desc = "/".join(n for n, _ in scope) or "books"
    print(f"命中 {total} 页 / {len(result)} 本书 (范围: {scope_desc}, 每页只列首处)\n")
    for r in result:
        b = r["book"]
        ws = f"[{b['_ws']}] " if b["_ws"] else ""
        print(f"■ {ws}{b['slug']}  《{b['title']}》  {b['pages']} 页 / {len(b['chapters'])} 章节")
        for h in r["hits"]:
            loc = f"{'workspaces/' + b['_ws'] + '/' if b['_ws'] else ''}" \
                  f"books/{b['slug']}/md/{h['page']}"
            chap = f"  §{h['chapter']}" if h["chapter"] else ""
            print(f"    {loc} (印刷 {h['printed']}, L{h['line']}){chap}")
            print(f"        {h['text']}")
        print()
    print("打开页面:  cat <上述路径>   |  章节表:  同目录 _toc.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
