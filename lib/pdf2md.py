#!/usr/bin/env python3
"""pdf2md - 将文字型 PDF 逐页转换为结构化 Markdown，并生成章节索引。

用法:
    python3 lib/pdf2md.py BOOK.pdf [-o books] [--slug SLUG] [--workers N] [--force] [--no-images]

产物 (写入 <out>/<slug>/):
    md/page_XXXX.md   逐页 Markdown (4 位页号 = PDF 页序, 1-based)
    md/images/        页面插图
    md/_toc.md        章节目录 (可读版, 供快速定位)
    index.json        元数据 + 章节树 + 页码三元映射
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

try:
    import pymupdf
except ImportError:  # noqa: BLE001
    sys.exit(
        "缺少依赖: pymupdf\n"
        "请先安装:\n"
        "  pip install -r requirements.txt\n"
        "  # 或: pip install pymupdf -i https://pypi.tuna.tsinghua.edu.cn/simple/"
    )

warnings.filterwarnings("ignore", message="Consider using the pymupdf_layout")
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()

TOOL_NAME = "pdf2md"
TOOL_VERSION = "2.0"

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
_GENERIC_STEMS = {"source", "book", "document", "output", "input", "file", "scan", "pdf"}


def read_meta_title(pdf_path: str) -> str:
    """读 PDF metadata 标题 (不渲染页面, 开销极小)。"""
    try:
        doc = pymupdf.open(pdf_path)
        title = (doc.metadata or {}).get("title") or ""
        doc.close()
        return title.strip()
    except Exception:
        return ""


def _slugify(text: str, limit: int = 44) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    if len(name) > limit:
        name = name[:limit].rstrip("-")
    return name


_GENERIC_PARENT_DIRS = frozenset(
    "books book pdfs pdf download downloads dl tmp temp out output inbox "
    "test tests data files documents document src source docs doc "
    "desktop test_output".split()
)


def derive_slug(pdf_path: str, title: str = "") -> str:
    """books/apoc/source.pdf -> 'apoc';  ~/dl/MyBook.pdf -> 'mybook'

    文件名是通用占位名 (source/book/...) 时:
      - 父目录名有意义 (books/apoc/source.pdf) -> 用父目录名, 目录结构即语义
      - 父目录也是容器名 (Downloads/source.pdf) -> 用 PDF 书名, 其次父目录名
    """
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    parent = os.path.basename(os.path.dirname(os.path.abspath(pdf_path)))
    if stem.lower() not in _GENERIC_STEMS:
        raw = stem
    elif parent and parent.lower() not in _GENERIC_PARENT_DIRS:
        raw = parent
    else:
        raw = title or parent or stem
    slug = _slugify(raw)
    if len(slug) < 3:  # 脏标题 (如单字母) 不够当标识, 退回目录名/文件名
        slug = _slugify(parent) or _slugify(stem)
    return slug or "book"


_BULLET = re.compile(r"^\s*(?:[•◦‣▪∙·▪○●\-–—\*+]|\d{1,3}[.)]|[a-zA-Z][.)])\s+\S")

# 图示/查询计划内的文字, 字号与标题相同但并非标题
_NOT_HEADING = re.compile(
    r"^:\S"
    r"|^\d+\s*(db hits|estimated rows|pagecache (hits|misses)|rows)"
    r"|\b(db hits|estimated rows|pagecache (hits|misses))$",
    re.I,
)


def is_mono(span: dict) -> bool:
    f = span.get("font", "").lower()
    return any(k in f for k in
               ("courier", "mono", "code", "consolata", "typewriter",
                "menlo", "andale mono", "lucida console"))


def dominant_size(line: dict) -> float:
    """行的主导字号, 按 0.5pt 分桶以吸收嵌入字体的浮点碎片。"""
    best, best_len = 0.0, -1
    for s in line["spans"]:
        t = s["text"].strip()
        if not t or is_mono(s):
            continue
        if len(t) > best_len:
            best, best_len = s["size"], len(t)
    if best == 0.0:
        for s in line["spans"]:
            t = s["text"].strip()
            if t and len(t) > best_len:
                best, best_len = s["size"], len(t)
    return round(round(best * 2) / 2, 1)


def line_plain_text(line: dict) -> str:
    return "".join(s["text"] for s in line["spans"]).strip()


def line_formatted_text(line: dict) -> str:
    """拼接行内 span，给等宽/粗体片段加 Markdown 标记。"""
    runs: list[tuple[str, str]] = []
    for s in line["spans"]:
        t = s["text"]
        if not t.strip():
            if runs:
                runs[-1] = (runs[-1][0], runs[-1][1] + t)
            continue
        if is_mono(s):
            kind = "code"
        elif s.get("flags", 0) & 16:
            kind = "bold"
        else:
            kind = "text"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + t)
        else:
            runs.append((kind, t))

    out = []
    for kind, text in runs:
        stripped = text.strip()
        if kind == "code":
            # 过长或含大量空白的等宽片段视为正文，不当行内代码
            prose_like = len(stripped) > 40 and stripped.count(" ") > len(stripped) // 4
            out.append(text if prose_like or not stripped else text.replace(stripped, f"`{stripped}`", 1))
        elif kind == "bold":
            out.append(text if len(stripped) > 100 or "**" in stripped
                       else text.replace(stripped, f"**{stripped}**", 1))
        else:
            out.append(text)
    return "".join(out).strip()


def box_inside(bbox, boxes) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for b in boxes:
        if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return True
    return False


def md_table(rows: list[list]) -> str:
    def cell(v) -> str:
        if v is None:
            return ""
        t = _WS.sub(" ", str(v)).strip()
        return t.replace("|", "\\|")

    rows = [[cell(c) for c in r] for r in rows if r is not None]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    if not any(header):
        header = [f"col{i + 1}" for i in range(width)]
        body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 第一遍: 扫描全书, 统计正文字号 / 标题字号 / 空文本页
# --------------------------------------------------------------------------
def scan_page(task) -> dict:
    path, idx = task
    size_chars: Counter = Counter()
    size_lines: Counter = Counter()
    size_short: Counter = Counter()
    size_pages: set = set()
    has_text = False

    doc = pymupdf.open(path)
    page = doc.load_page(idx)

    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for line in b["lines"]:
            raw = line_plain_text(line)
            if not raw:
                continue
            sz = dominant_size(line)
            if sz:
                mono_only = all(is_mono(s) for s in line["spans"] if s["text"].strip())
                if not mono_only:
                    total = sum(len(s["text"].strip()) for s in line["spans"]
                                if s["text"].strip() and not is_mono(s))
                    if total:
                        has_text = True
                        size_chars[sz] += total
                        size_lines[sz] += 1
                        if len(raw) <= 100:
                            size_short[sz] += 1
                        size_pages.add(sz)
            if any(s["text"].strip() for s in line["spans"]):
                has_text = True
    doc.close()
    return {
        "size_chars": size_chars,
        "size_lines": size_lines,
        "size_short": size_short,
        "size_pages": {k: 1 for k in size_pages},
        "has_text": has_text,
    }


def build_profile(per_page: list[dict], page_count: int) -> dict:
    size_chars: Counter = Counter()
    size_lines: Counter = Counter()
    size_short: Counter = Counter()
    size_pages: Counter = Counter()
    empty_pages = []

    for i, s in enumerate(per_page):
        size_chars.update(s["size_chars"])
        size_lines.update(s["size_lines"])
        size_short.update(s["size_short"])
        size_pages.update(s["size_pages"])
        if not s["has_text"]:
            empty_pages.append(i + 1)

    if not size_chars:
        raise SystemExit("错误: 未能从 PDF 中提取任何文本 (可能是扫描件, 需先 OCR)")

    body_size = max(size_chars.items(), key=lambda kv: kv[1])[0]

    cands = []
    for sz, chars in size_chars.items():
        if sz <= body_size + 0.4:
            continue
        if chars < 20:
            continue
        if size_pages[sz] < 2 and chars < 100:
            continue
        if size_lines[sz] and size_short[sz] / size_lines[sz] < 0.4:
            continue
        cands.append(sz)
    cands.sort(reverse=True)

    headings = {f"{sz:.1f}": min(i + 1, 4) for i, sz in enumerate(cands)}

    return {
        "body_size": body_size,
        "headings": headings,
        "empty_pages": empty_pages,
    }


# --------------------------------------------------------------------------
# 层级校准: 用 PDF 书签标题反查正文行, 修正字号 -> 标题层级的映射
# --------------------------------------------------------------------------
def _norm_title(t: str) -> str:
    return _WS.sub(" ", t).strip().lower().rstrip(".:：").strip()


def _probe_group(group: list) -> list:
    votes: list[tuple[float, int]] = []
    doc = None
    for path, idx, target, level in group:
        try:
            if doc is None:
                doc = pymupdf.open(path)
            page = doc.load_page(idx)
        except Exception:
            continue
        found = False
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0 or found:
                continue
            for line in b["lines"]:
                raw = _norm_title(line_plain_text(line))
                if not raw or len(raw) < 4:
                    continue
                hit = raw == target or (
                    len(raw) >= 10 and len(raw) >= 0.7 * len(target) and target.startswith(raw)
                )
                if hit:
                    size = dominant_size(line)
                    if size:
                        votes.append((round(size, 1), level))
                    found = True
                    break
    if doc is not None:
        doc.close()
    return votes


def calibrate_headings(path: str, toc: list, profile: dict, workers: int) -> dict:
    """让书签声明的层级覆盖字号启发式 (仅在投票充分时)。"""
    if not toc:
        return profile

    # 排除目录页: 目录条目文字会与书签同名, 但字号是目录字号
    exclude: set[int] = set()
    for i, (level, ctitle, pageno) in enumerate(toc):
        if re.search(r"contents|目录|table of ?contents", ctitle, re.I):
            end = toc[i + 1][2] if i + 1 < len(toc) else pageno + 5
            exclude.update(range(pageno, min(end, pageno + 12) + 1))

    groups: list[list] = []
    cur: list = []
    for level, ctitle, pageno in toc:
        if pageno in exclude or pageno < 1:
            continue
        cur.append((path, pageno - 1, _norm_title(ctitle), int(level)))
        if len(cur) >= 24:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    if not groups:
        return profile

    votes: list[tuple[float, int]] = []
    if workers > 1 and len(groups) > 4:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_probe_group, groups, chunksize=2):
                votes.extend(part)
    else:
        for g in groups:
            votes.extend(_probe_group(g))

    body = profile["body_size"]
    per_size: dict[float, Counter] = {}
    for size, level in votes:
        if size > body + 0.4:
            per_size.setdefault(size, Counter())[level] += 1

    headings = dict(profile["headings"])
    calibrated = 0
    for size, counter in per_size.items():
        total = sum(counter.values())
        top, n = counter.most_common(1)[0]
        if total >= 2 and n >= total * 0.7:
            key = f"{size:.1f}"
            if headings.get(key) != top:
                headings[key] = top
                calibrated += 1

    if headings:
        profile["headings"] = dict(sorted(headings.items(), key=lambda kv: -float(kv[0])))
        profile["heading_calibrated"] = calibrated
        profile["heading_votes"] = len(votes)
    return profile


# --------------------------------------------------------------------------
# 第二遍: 渲染单页
# --------------------------------------------------------------------------
def render_page(task) -> tuple[int, str | None, dict]:
    path, idx, profile, images_dir, with_images, title = task
    pdf_page = idx + 1

    doc = pymupdf.open(path)
    page = doc.load_page(idx)

    headings = {float(k): v for k, v in profile["headings"].items()}
    height = page.rect.height

    out: list[str] = []
    para: list[str] = []
    para_end_y = None
    para_size = 10.0
    code: list[str] = []
    bullet: list[str] | None = None
    bullet_end_y = None
    stats = {"tables": 0, "images": 0, "code_blocks": 0, "list_items": 0}
    img_seq = 0

    def ensure_blank():
        if out and not out[-1].endswith("\n\n"):
            out[-1] += "\n"

    def flush_para():
        nonlocal para, para_end_y
        if not para:
            return
        merged = ""
        for piece in para:
            if not merged:
                merged = piece
            elif merged.endswith("-") and piece[:1].islower():
                merged = merged[:-1] + piece
            else:
                merged = merged + " " + piece
        ensure_blank()
        out.append(merged + "\n\n")
        para = []
        para_end_y = None

    def flush_code():
        nonlocal code
        if code:
            ensure_blank()
            out.append("```text\n" + "\n".join(code) + "\n```\n\n")
            stats["code_blocks"] += 1
            code = []

    def flush_bullet():
        nonlocal bullet, bullet_end_y
        if bullet:
            # 列表项之间保持连续, 由下一个 flush_* 负责补空行
            out.append("- " + "".join(bullet).strip() + "\n")
            stats["list_items"] += 1
            bullet = None
            bullet_end_y = None

    def flush_all():
        flush_bullet()
        flush_para()
        flush_code()

    tables = []
    try:
        tables = list(page.find_tables().tables)
    except Exception:
        tables = []
    table_boxes = [t.bbox for t in tables]

    # 阅读顺序: 保持 PyMuPDF 块顺序, 表格插入到首个 y0 更大的块之前
    blocks = page.get_text("dict")["blocks"]
    tsorted = sorted(tables, key=lambda t: t.bbox[1])
    merged: list[tuple[str, object]] = []
    ti = 0
    for b in blocks:
        while ti < len(tsorted) and tsorted[ti].bbox[1] < b["bbox"][1]:
            merged.append(("table", tsorted[ti]))
            ti += 1
        merged.append(("block", b))
    while ti < len(tsorted):
        merged.append(("table", tsorted[ti]))
        ti += 1

    for kind, payload in merged:
        if kind == "table":
            flush_all()
            try:
                rows = payload.extract()
            except Exception:
                rows = None
            if rows:
                md = md_table(rows)
                if md:
                    out.append(md + "\n")
                    stats["tables"] += 1
            continue

        b = payload
        if b["type"] == 1:
            if with_images and b.get("image"):
                flush_all()
                img_seq += 1
                ext = b.get("ext", "png") or "png"
                fname = f"image_{pdf_page:04d}_{img_seq:03d}.{ext}"
                with open(os.path.join(images_dir, fname), "wb") as fh:
                    fh.write(b["image"])
                w, h = b.get("width", 0), b.get("height", 0)
                alt = f"第 {pdf_page} 页插图 {img_seq}"
                out.append(f"![{alt}](images/{fname})\n\n")
                stats["images"] += 1
            continue

        if box_inside(b["bbox"], table_boxes):
            continue

        for line in b["lines"]:
            raw = line_plain_text(line)
            if not raw:
                continue
            y0, y1 = line["bbox"][1], line["bbox"][3]
            # 页脚的页码数字原样保留: 任何"猜页码再删除"的启发式都无法保证零误删,
            # 而结构化页码 (page_map / 书签) 本来就是确定性读取, 不依赖这里。

            sz = dominant_size(line)
            mono_chars = sum(len(s["text"].strip()) for s in line["spans"]
                              if s["text"].strip() and is_mono(s))
            all_chars = sum(len(s["text"].strip()) for s in line["spans"] if s["text"].strip())
            mono_ratio = (mono_chars / all_chars) if all_chars else 0.0

            level = headings.get(sz)
            if level and len(raw) <= 120 and not _NOT_HEADING.match(raw):
                flush_all()
                out.append(f"{'#' * level} {raw}\n\n")
                continue

            if mono_ratio >= 0.75:
                flush_bullet()
                flush_para()
                code.append(raw)
                continue

            flush_code()
            text = line_formatted_text(line)
            if not text:
                continue

            if _BULLET.match(raw):
                flush_para()
                flush_bullet()
                bullet = [re.sub(
                    r"^\s*(?:[•◦‣▪∙·○●\-*+]|─|\d{1,3}[.)]|[a-zA-Z][.)])\s+", "", raw)]
                bullet_end_y = y1
                continue

            if bullet is not None and bullet_end_y is not None \
                    and (y0 - bullet_end_y) <= 0.9 * (sz or para_size):
                bullet.append(" " + text)
                bullet_end_y = y1
                continue

            flush_bullet()
            if para and para_end_y is not None and (y0 - para_end_y) <= 0.9 * para_size:
                para.append(text)
            else:
                flush_para()
                para.append(text)
                para_size = sz or para_size
            para_end_y = y1

    flush_all()

    body = "".join(out).strip()
    header = f"# {title} - 第 {pdf_page} 页\n\n"
    content = header + (body if body else "*（本页无可提取文本）*")
    content += "\n"
    doc.close()
    return idx, content, stats


# --------------------------------------------------------------------------
# 索引生成
# --------------------------------------------------------------------------
def build_index(doc, pdf_path: str, slug: str, profile: dict, md_dir: str,
                page_count: int) -> dict:
    title = doc.metadata.get("title") or os.path.splitext(os.path.basename(pdf_path))[0]
    author = doc.metadata.get("author") or ""

    chapters = []
    for level, ctitle, pageno in doc.get_toc():
        pageno = max(1, min(pageno, page_count))
        page = doc.load_page(pageno - 1)
        chapters.append({
            "level": int(level),
            "title": ctitle.strip(),
            "pdf_page": pageno,
            "printed": page.get_label(),
            "md": f"md/page_{pageno:04d}.md",
        })

    page_map = []
    for i in range(page_count):
        page = doc.load_page(i)
        page_map.append({
            "pdf": i + 1,
            "printed": page.get_label(),
            "md": f"md/page_{i + 1:04d}.md",
        })

    return {
        "slug": slug,
        "title": title,
        "author": author,
        "source_pdf": os.path.basename(pdf_path),
        "converted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": f"{TOOL_NAME} {TOOL_VERSION}",
        "pages": page_count,
        "page_numbering": (
            "md 文件名 page_XXXX.md = PDF 页序(1-based); "
            "printed = PDF 印刷页码 (get_label()); 完整映射见 page_map"
        ),
        "profile": profile,
        "chapters": chapters,
        "page_map": page_map,
        "stats": {},
    }


def write_toc_md(index: dict, path: str) -> None:
    lines = [
        f"# {index['title']} - 章节目录",
        "",
        f"> 来源: `{index['source_pdf']}` | 共 {index['pages']} 页 | "
        f"转换: {index['converted_at']} | 工具: {index['tool']}",
        ">",
        "> 页码: `page_XXXX.md` = PDF 页序(1-based)；印刷页码见括号内。",
        "",
    ]
    for c in index["chapters"]:
        indent = "  " * max(0, c["level"] - 1)
        lines.append(
            f"{indent}- **{c['title']}** — PDF p.{c['pdf_page']} "
            f"(印刷 {c['printed']}) → [{c['md']}]({c['md']})"
        )
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------
# 脚手架: 首次转换自动生成 AGENTS.md 骨架并登记 catalog.md, 免手工准备
# --------------------------------------------------------------------------
def _extract_version(title: str) -> str:
    for pat in (r"\bv?(\d{4}[.\-]\d{2})\b", r"\bv(\d+[.\d]+)\b",
                r"(?<![\d.])(\d{2})(?![\d.])"):
        m = re.search(pat, title)
        if m:
            return m.group(1)
    return "—"


def _agents_skeleton(index: dict) -> str:
    chapters = index["chapters"]
    lv1 = [c for c in chapters
           if c["level"] == 1
           and c["title"] != index["title"]
           and "table of contents" not in c["title"].lower()]

    offset = None
    for m in index["page_map"]:
        if m["printed"].isdigit():
            offset = m["pdf"] - int(m["printed"])
            break

    lines = [
        f"# {index['title']} · `{index['slug']}`",
        "",
        f"- 来源: `{index['source_pdf']}` · {index['pages']} 页 · "
        f"{len(chapters)} 个书签章节 · 转换: {index['tool']}",
    ]
    if offset:
        lines.append(
            f"- 页码: 印刷页 1 = PDF p.{offset + 1}"
            f"（**印刷页 = PDF 页序 − {offset}**）"
        )
    lines += [
        "- 映射: [index.json](index.json) · 章节: [md/_toc.md](md/_toc.md)",
        "",
        "## 该书入口（书签页码 = PDF 页序 = md 文件号）",
        "",
        "| 主题 | 起始页 |",
        "|---|---|",
    ]
    for c in lv1[:12]:
        lines.append(f"| {c['title']} | p.{c['pdf_page']} |")
    lines += [
        "",
        "## 该书已知缺陷 / 注意",
        "",
        "- （待补充：图内文字、表格断词、标题识别偏差等，参考其他书的 AGENTS.md 写法）",
        "- 完整章节表见 [md/_toc.md](md/_toc.md)；页码换算见 [index.json](index.json) 的 `page_map`。",
        "",
    ]
    return "\n".join(lines)


def _catalog_line(index: dict) -> str:
    lv1 = [c["title"] for c in index["chapters"]
           if c["level"] == 1
           and c["title"] != index["title"]
           and "table of contents" not in c["title"].lower()]
    topic = "、".join(lv1[:3]) if lv1 else "—"
    if len(topic) > 60:
        topic = topic[:58] + "…"
    return (f"| `{index['slug']}` | {index['title']} | {_extract_version(index['title'])} | "
            f"{index['pages']} | {len(index['chapters'])} | {topic} | "
            f"[\\_toc.md](books/{index['slug']}/md/_toc.md) |")


_CATALOG_HEADER = """# 文档目录

全部书籍清单。定位章节请进 `books/<slug>/md/_toc.md`；页码映射见 `books/<slug>/index.json`。

| slug | 书名 | 版本 | 页数 | 章节 | 主题 | 入口 |
|---|---|---|---|---|---|---|
"""


def _append_workspace_route(index: dict, out_root: str) -> str | None:
    """向工作区 AGENTS.md 的「本区书目路由」表追加一行 (已有则不重复)。"""
    ws_dir = os.path.dirname(os.path.abspath(out_root))
    if os.path.basename(ws_dir) != "workspaces" and \
            os.path.basename(os.path.dirname(ws_dir)) != "workspaces":
        return None
    path = os.path.join(ws_dir, "AGENTS.md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    slug = index["slug"]
    topic = "、".join(c["title"] for c in index["chapters"]
                     if c["level"] == 1
                     and c["title"] != index["title"]
                     and "table of contents" not in c["title"].lower())
    topic = (topic[:48] + "…") if len(topic) > 50 else (topic or "—")
    row = f"| {topic} | `{slug}` | [\\_toc.md](books/{slug}/md/_toc.md) |"
    if any(f"`{slug}`" in ln for ln in lines):
        return None

    heading = next((i for i, ln in enumerate(lines)
                    if ln.startswith("## 本区书目路由")), None)
    if heading is None:
        return None
    sep = next((i for i in range(heading, len(lines))
                if lines[i].startswith("|---")), None)
    if sep is None:
        return None
    pos = sep + 1
    while pos < len(lines) and lines[pos].startswith("|"):
        pos += 1
    if pos - 1 > sep and "自动追加" in lines[pos - 1]:
        lines[pos - 1] = row      # 首次导入, 替换占位行
    else:
        lines.insert(pos, row)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def write_book_scaffold(index: dict, book_dir: str, out_root: str) -> list[str]:
    """只在缺失时创建, 绝不覆盖已有手工内容。"""
    created: list[str] = []
    slug = index["slug"]

    agents = os.path.join(book_dir, "AGENTS.md")
    if not os.path.exists(agents):
        with open(agents, "w", encoding="utf-8") as f:
            f.write(_agents_skeleton(index))
        created.append(os.path.relpath(agents))

    # catalog 只在标准结构 (输出根目录名为 books) 下登记, 避免污染临时目录
    if os.path.basename(os.path.abspath(out_root)) == "books":
        repo = os.path.dirname(os.path.abspath(out_root))
        catalog = os.path.join(repo, "catalog.md")
        marker = f"`{slug}`"
        if os.path.exists(catalog):
            with open(catalog, encoding="utf-8") as f:
                text = f.read()
            if marker not in text:
                if not text.endswith("\n"):
                    text += "\n"
                with open(catalog, "w", encoding="utf-8") as f:
                    f.write(text + _catalog_line(index) + "\n")
                created.append(os.path.relpath(catalog))
        else:
            with open(catalog, "w", encoding="utf-8") as f:
                f.write(_CATALOG_HEADER + _catalog_line(index) + "\n")
            created.append(os.path.relpath(catalog))

    route = _append_workspace_route(index, out_root)
    if route:
        created.append(os.path.relpath(route))
    return created


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def find_pdfs(sources: list[str]) -> list[str]:
    """支持任意组合: 文件、目录、多参数混合; 同一文件只处理一次。"""
    out: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if os.path.isfile(src):
            if not src.lower().endswith(".pdf"):
                raise SystemExit(f"错误: 不是 PDF 文件: {src}")
            found = [src]
        elif os.path.isdir(src):
            found = []
            for root, _dirs, files in os.walk(src):
                found += [os.path.join(root, f)
                          for f in sorted(files) if f.lower().endswith(".pdf")]
            found = sorted(found)
        else:
            raise SystemExit(f"错误: 路径不存在: {src}")
        for p in found:
            key = os.path.realpath(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def convert(pdf_path: str, out_root: str, slug: str, workers: int,
            force: bool, with_images: bool, quiet: bool = False) -> dict:
    doc = pymupdf.open(pdf_path)
    page_count = doc.page_count
    title = doc.metadata.get("title") or os.path.splitext(os.path.basename(pdf_path))[0]
    toc = doc.get_toc()
    doc.close()

    book_dir = os.path.join(out_root, slug)
    md_dir = os.path.join(book_dir, "md")
    images_dir = os.path.join(md_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # 源 PDF 一旦更换, 旧的增量结果不可信 -> 强制全量重转, 避免新旧内容混杂
    source_size = os.path.getsize(pdf_path)
    prev_index = os.path.join(book_dir, "index.json")
    if os.path.exists(prev_index):
        try:
            with open(prev_index, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("source_size") and prev["source_size"] != source_size:
                if not quiet:
                    print(f"  注意: {book_dir}/source.pdf 已更换 "
                          f"({prev['source_size']} -> {source_size} 字节), "
                          f"清空旧产物并全量重转")
                # 页数可能不同, 必须清掉旧页, 否则残留上一本书的多余页面
                shutil.rmtree(md_dir, ignore_errors=True)
                os.makedirs(images_dir, exist_ok=True)
                force = True
        except Exception:
            pass

    t0 = time.time()
    tasks = [(pdf_path, i) for i in range(page_count)]

    if not quiet:
        print(f"[1/3] 扫描版式: {os.path.basename(pdf_path)} ({page_count} 页)")
    if workers > 1 and page_count > 40:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            per_page = list(ex.map(scan_page, tasks, chunksize=16))
    else:
        per_page = [scan_page(t) for t in tasks]
    profile = build_profile(per_page, page_count)
    profile = calibrate_headings(pdf_path, toc, profile, workers)
    if not quiet:
        print(f"      正文字号={profile['body_size']} "
              f"标题层级={profile['headings'] or '无'} "
              f"(书签校准 {profile.get('heading_calibrated', 0)}/{profile.get('heading_votes', 0)} 票) "
              f"空文本页={len(profile['empty_pages'])}")

    pdf_mtime = os.path.getmtime(pdf_path)
    render_tasks = []
    for i in range(page_count):
        md_path = os.path.join(md_dir, f"page_{i + 1:04d}.md")
        if not force and os.path.exists(md_path) and os.path.getmtime(md_path) >= pdf_mtime:
            continue
        render_tasks.append((pdf_path, i, profile, images_dir, with_images, title))

    if not quiet:
        print(f"[2/3] 渲染: 新增/重转 {len(render_tasks)} 页, 跳过 {page_count - len(render_tasks)} 页")

    totals = {"tables": 0, "images": 0, "code_blocks": 0, "list_items": 0}
    skipped = page_count - len(render_tasks)
    if render_tasks:
        if workers > 1 and len(render_tasks) > 40:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = ex.map(render_page, render_tasks, chunksize=8)
        else:
            results = map(render_page, render_tasks)
        for idx, content, stats in results:
            with open(os.path.join(md_dir, f"page_{idx + 1:04d}.md"), "w", encoding="utf-8") as f:
                f.write(content)
            for k in totals:
                totals[k] += stats[k]

    if not quiet:
        print(f"[3/3] 生成索引")
    doc = pymupdf.open(pdf_path)
    index = build_index(doc, pdf_path, slug, profile, md_dir, page_count)
    doc.close()

    if not with_images and os.path.isdir(images_dir):
        shutil.rmtree(images_dir, ignore_errors=True)
    existing_imgs = 0
    if os.path.isdir(images_dir):
        existing_imgs = len([f for f in os.listdir(images_dir) if f.startswith("image_")])

    index["stats"] = {
        **totals,
        "images_on_disk": existing_imgs,
        "pages_converted": len(render_tasks),
        "pages_skipped": skipped,
        "chapters": len(index["chapters"]),
    }
    index["source_size"] = source_size
    with open(os.path.join(book_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    write_toc_md(index, os.path.join(md_dir, "_toc.md"))

    scaffolded = write_book_scaffold(index, book_dir, out_root)

    if not quiet:
        dt = time.time() - t0
        print(f"完成: {book_dir}")
        print(f"  页面 {page_count} (新转 {len(render_tasks)}/跳过 {skipped}) | "
              f"章节 {len(index['chapters'])} | 表格 {totals['tables']} | "
              f"代码块 {totals['code_blocks']} | 列表项 {totals['list_items']} | "
              f"图片 {existing_imgs} | 空文本页 {len(profile['empty_pages'])} | {dt:.1f}s")
        for path in scaffolded:
            rel = os.path.relpath(path, os.getcwd())
            print(f"  新建: {rel if not rel.startswith('..' + os.sep) else path}")
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog=f"{TOOL_NAME}",
        description="将文字型 PDF 逐页转换为结构化 Markdown 并生成章节索引。",
    )
    ap.add_argument("source", nargs="+", help="PDF 文件或目录, 可给多个 (混合亦可)")
    ap.add_argument("-o", "--out", default="books", help="输出根目录 (默认: books)")
    ap.add_argument("--slug", default=None, help="书籍标识 (默认从文件名派生; 仅单个 PDF 时可用)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1, help="并行进程数")
    ap.add_argument("--force", action="store_true", help="忽略增量, 强制重转全部页面")
    ap.add_argument("--no-images", action="store_true", help="不提取图片")
    ap.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    args = ap.parse_args(argv)

    pdfs = find_pdfs(args.source)
    if not pdfs:
        raise SystemExit("错误: 未找到 PDF 文件")
    if len(pdfs) > 1 and args.slug:
        raise SystemExit("错误: 批量转换时不能指定 --slug")

    ok = 0
    used_slugs: dict[str, str] = {}
    for pdf in pdfs:
        slug = args.slug or derive_slug(pdf, read_meta_title(pdf))
        if slug in used_slugs and len(pdfs) > 1:
            print(f"错误: slug 冲突 '{slug}': {used_slugs[slug]} 与 {pdf} 派生出同一标识; "
                  f"请重命名文件或改为逐个导入并指定 --slug", file=sys.stderr)
            continue
        used_slugs[slug] = pdf
        try:
            convert(pdf, args.out, slug, max(1, args.workers), args.force,
                    not args.no_images, args.quiet)
            ok += 1
        except SystemExit:
            raise
        except Exception as e:
            print(f"错误: 处理 {pdf} 失败: {e}", file=sys.stderr)
        if len(pdfs) > 1 and not args.quiet:
            print()
    print(f"共 {len(pdfs)} 个文件, 成功 {ok} 个")
    return 0 if ok == len(pdfs) else 1


if __name__ == "__main__":
    sys.exit(main())
