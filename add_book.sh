#!/usr/bin/env bash
# 向工作区导入书籍 (工作区不存在则自动创建, 同名则追加)
#
# 用法:
#   ./add_book.sh <workspace> BOOK.pdf               # 单本
#   ./add_book.sh <workspace> a.pdf b.pdf            # 多本
#   ./add_book.sh <workspace> /path/manuals-dir      # 一个目录 (递归)
#   ./add_book.sh <workspace> BOOK.pdf --slug my-slug
#
# 示例:
#   ./add_book.sh csg ~/Downloads/*.pdf
#   ./add_book.sh csg newbook.pdf          # csg 已存在 -> 追加
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "错误: 需要 python3" >&2; exit 1; }
python3 -c "import pymupdf" 2>/dev/null || {
  echo "缺少依赖 pymupdf, 请先运行:  pip install -r requirements.txt" >&2; exit 1; }

usage() {
  echo "用法: $0 <workspace> <PDF|目录> [更多 PDF|目录] [--slug my-slug]" >&2
  echo "  workspace: 小写字母/数字/连字符, 表示这批书的归属 (如 csg, ml-systems)" >&2
  echo "  示例: $0 csg ~/Downloads/*.pdf" >&2
  exit 1
}

[[ $# -ge 2 ]] || usage

WS=$1
shift

if [[ ! "$WS" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]]; then
  echo "错误: 非法 workspace 名 '$WS' (只允许小写字母/数字/连字符, 且以字母或数字开头)" >&2
  exit 1
fi
case "$WS" in
  lib|workspaces|books) echo "错误: '$WS' 是保留名, 换一个" >&2; exit 1 ;;
esac

WS_DIR="workspaces/$WS"
if [[ -d "$WS_DIR" ]]; then
  echo "工作区已存在: $WS_DIR/  (追加导入)"
else
  mkdir -p "$WS_DIR/books"
  cat > "$WS_DIR/AGENTS.md" <<'EOF'
# 工作区 `{{WS}}` · 问答规则

本目录是一批**相互独立**的书籍, 与仓库内其他 `workspaces/*` 无关。

## 你 (AI) 的范围约束

- 只读取与引用本目录 (`workspaces/{{WS}}/`) 下的文件: `catalog.md`、`books/**`。
- 仓库里其他 `workspaces/<name>/` 属于**无关项目**: 不读取、不提及、不作为上下文。
- 通用问答规则 (严格基于原文、观点必须标注、引用格式、页码规范、已知局限)
  见仓库根 `AGENTS.md`, 按那里的规则执行。

## 本区操作

```bash
# 检索 (在本工作区内执行, 自动只搜本区)
python3 ../../lib/search.py "关键词"
python3 ../../lib/search.py "关键词" -n 5        # 每书最多 5 条; -r 正则; --json

# 导入新书 (在仓库根执行)
./add_book.sh {{WS}} ~/Downloads/newbook.pdf

# 删除书籍 (在仓库根执行; 会同步清理本表与 catalog.md)
./remove_book.sh {{WS}} <slug>          # -y 免确认, --dry-run 只看
```

## 本区书目路由

| 主题 | slug | 入口 |
|---|---|---|
| （导入书籍后自动追加） | | |
EOF
  sed -i '' "s/{{WS}}/$WS/g" "$WS_DIR/AGENTS.md"
  echo "已创建工作区: $WS_DIR/  (books/ + AGENTS.md)"
fi

# 兼容旧的位置参数用法: BOOK.pdf my-slug  ->  --slug my-slug
ARGS=("$@")
if [[ $# -eq 2 && ! -e "${ARGS[1]}" && "${ARGS[1]}" != -* \
      && "${ARGS[1]}" != */* && "${ARGS[1]}" != *.[Pp][Dd][Ff] ]]; then
  echo "提示: 位置参数已改为 --slug, 本次按 --slug \"${ARGS[1]}\" 处理" >&2
  ARGS=("${ARGS[0]}" --slug "${ARGS[1]}")
fi

python3 lib/pdf2md.py "${ARGS[@]}" -o "$WS_DIR/books"

echo
echo "工作区 $WS: 导入完成"
echo "  书目:   $WS_DIR/catalog.md"
echo "  规则:   $WS_DIR/AGENTS.md"
echo "  检索:   python3 ../../lib/search.py \"关键词\"   (cd $WS_DIR 后)"
