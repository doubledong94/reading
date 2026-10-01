#!/usr/bin/env bash
# 从工作区删除书籍 (删除 books/<slug>/ 并清理 catalog.md / AGENTS.md 登记)
#
# 用法:
#   ./remove_book.sh <workspace> <slug> [<slug> ...]   # 交互确认
#   ./remove_book.sh <workspace> <slug> -y             # 免确认 (脚本/AI)
#   ./remove_book.sh <workspace> <slug> --dry-run      # 只看将删除什么
#
# 示例:
#   ./remove_book.sh neo4j neo4j-status-codes-2026-09
#   ./remove_book.sh neo4j old-a old-b -y
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "错误: 需要 python3" >&2; exit 1; }

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <workspace> <slug> [<slug> ...] [-y|--dry-run]" >&2
  echo "  示例: $0 neo4j neo4j-status-codes-2026-09" >&2
  exit 1
fi

exec python3 lib/remove_book.py "$@"
