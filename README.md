# 多书 PDF 文档语料库（AI 辅助阅读/问答）

把任意**文字型 PDF** 转成结构化 Markdown + 章节索引，按「工作区」隔离成互不相干的批次，
供 AI 严格基于原文回答问题。

## 操作步骤

### 1. 准备环境（只需一次）

```bash
cd <仓库根>
pip install -r requirements.txt
# 国内: pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

### 2. 导入书籍（第一个参数 = 工作区名，即这批书的归属）

```bash
./add_book.sh csg ~/Downloads/One-Book.pdf        # 单本
./add_book.sh csg ~/Downloads/*.pdf               # 多本 (shell 展开)
./add_book.sh csg ~/Downloads/manuals             # 整个目录 (递归找 PDF)
./add_book.sh ml-sys ~/Downloads/ml/*.pdf         # 另一批书 -> 另一个工作区
./add_book.sh csg book.pdf --slug my-slug         # 可选: 手动指定 slug (缺省取文件名)
```

- 工作区不存在则**自动创建**（`workspaces/<name>/` + 区级 `AGENTS.md` 骨架）；已存在则直接追加
- PDF 不必搬进仓库，直接给路径
- **检查输出**：`页面 241` 应与 PDF 实际页数一致、`章节 > 0`（为 0 多半是扫描件，无文字层会直接报错）
- 自动产出（无手工步骤）：`books/<slug>/{md/, index.json, AGENTS.md}`、区 `catalog.md`、区 `AGENTS.md` 书目路由

### 3. 进入工作区 —— 这个目录就是 AI 的可见范围

```bash
cd workspaces/csg
```

### 4. 阅读定位（固定三跳）

| 跳 | 文件 | 用途 |
|---|---|---|
| ① 选书 | `catalog.md` | 本区有哪些书、各是什么主题 |
| ② 定章节 | `books/<slug>/md/_toc.md` | 章节目录（页码 = PDF 页序 = 文件名） |
| ③ 读内容 | `books/<slug>/md/page_0042.md` | 具体某页；印刷页码见 `index.json` 的 `page_map` |

### 5. 检索（范围自动跟随当前目录）

```bash
python3 ../../lib/search.py "PathExpander"      # 在区内: 只搜本区
python3 ../../lib/search.py "MERGE" -n 5        # -s 限定书  -r 正则  --json
# 在仓库根: python3 lib/search.py "MERGE" -w csg
```

命中行直接给出 `workspaces/<ws>/books/<slug>/md/page_0056.md (印刷 52) §章节名`，按第 4 步打开即可。

### 6. 启动 AI 提问

在第 3 步的目录里启动 Claude Code / opencode：

```
> 查 apoc.path.expandConfig 的全部配置项，页码标出来。
```

AI 读本区 `AGENTS.md`（书目路由 + 只读本区）与根 `AGENTS.md`（问答规则），
只依据本区 `books/*/md/` 回答，引用形如
`workspaces/csg/books/apoc/md/page_0042.md, 印刷 p.20, §Configuration Options`。

### 7. 日常维护

- **换新版 PDF**：重跑第 2 步同一条命令——源文件变了会自动清空旧页全量重转，不残留
- **删除某本书**：`./remove_book.sh csg <slug>`（交互确认；`-y` 免确认，`--dry-run` 只看）。
  三合一删除：删 `books/<slug>/` 目录，并清掉该区 `catalog.md`、`AGENTS.md` 书目路由里的对应行
- **新批次书**：换个工作区名再跑第 2 步，与旧区互不相干
- **临时调参**（强制重转/关图片/并行度）：
  `python3 lib/pdf2md.py a.pdf -o workspaces/csg/books --force --workers 8 --no-images`

## 工作区（批次隔离的概念）

- 一批互不相干的书 = 一个 `workspaces/<name>/`，互不引用、互不提及，就像两个项目；
  工具只有仓库根一份（`add_book.sh` + `lib/`），区内无脚本副本
- AI 在哪区启动就只见哪区：其他 `workspaces/*` 按规则不读取、不作为上下文
- 跨区检索需显式 `-w`；同一区内多本书天然共读
- `workspaces/` 整体不入库（含 `books/`、区 `catalog.md`），仓库只跟踪工具与规则文档

## 目录结构

```
workspaces/<workspace>/           一批互不相干的书
├── AGENTS.md                     该区约束 + 书目路由 (AI 启动时读到它)
├── catalog.md                    该区书目 (本地生成, 不入库)
└── books/<slug>/
    ├── source.pdf                原始 PDF
    ├── index.json                元数据 + 章节树(书签) + 页码三元映射 page_map
    ├── AGENTS.md                 该书入口与已知缺陷
    └── md/
        ├── page_XXXX.md          逐页 Markdown（4 位页号 = PDF 页序, 1-based）
        ├── _toc.md               章节目录（可读版，含页码与链接）
        └── images/               页面插图
add_book.sh                       导入入口 (第一参数 = 工作区名)
remove_book.sh                    删除入口 (第一参数 = 工作区名)
lib/pdf2md.py                     转换器（CLI）
lib/remove_book.py                删除书籍 + 清理 catalog/AGENTS 登记
lib/search.py                     检索（关键词 → 工作区/书/页/章节）
AGENTS.md                         通用问答规则 + 工作区机制 (AI 先读这个)
requirements.txt                  依赖（pymupdf）
READING_PLAN.md                   学习计划（本地文件, 不入库）
```

## 转换器能力

| 能力 | 实现 |
|---|---|
| 逐页 Markdown | 4 位补零页名，排序天然正确（>999 页不乱序） |
| 章节索引 | 读 PDF 书签 → `_toc.md` + `index.json.chapters`（层级/起始页/链接） |
| 页码三元映射 | `page_map`: PDF 页序 ↔ 印刷页码(`get_label`) ↔ md 文件 |
| 表格恢复 | `find_tables()` → GFM 表格（原为塌陷的纯文本） |
| 标题层级 | 字号统计 + **书签标题反查校准**（书签为准） |
| 代码块 | 等宽字体行聚合成 fenced code |
| 列表 | `•`/`-`/`1.` 行识别为 Markdown 列表项 |
| 图片 | dict 块内嵌图片按阅读顺序内联 |
| 增量 | 源 PDF 未变则跳过已转页面（`--force` 忽略；**源文件更换会自动清空重转**，不残留旧页） |
| 并行 | 多进程按页扫描与渲染 |
| 转换报告 | 页数/章节/表格/代码块/列表/图片/空文本页/耗时 |
| 脚手架 | 首次转换自动写书级 `AGENTS.md` 骨架、登记区 `catalog.md` 与区 `AGENTS.md` 路由（已有手工内容不覆盖） |

## 刻意不做的事与已知局限

- **不删页脚页码 / 不删页眉页脚**：任何"猜页码再删除"的启发式都无法保证零误删正文
  （实测 4 本书 0 命中、纯风险）；需要页码时走 `index.json` 的 `page_map`（确定性读取）
- **不 OCR**：扫描件提取不到文本会直接报错退出，需先 OCR
- **不跨页合并段落**：一页一个文件，保证定位精确
- **图内文字**：示意图、查询计划图中的文字会混入正文（`16 db hits`、`:ACTED_IN` 等），
  已排除其被误判为标题，但仍出现在页面文本中
- **表格断词**：单元格内换行转为空格，个别断词残留空格（`from_fi le_urls`）

## 问答规则

见 [AGENTS.md](AGENTS.md)：工作区机制、定位流程、页码规范（`page_XXXX` / 印刷页 / 章节页）、
引用格式，以及三条铁律——**严格基于文档内容 / 观点必须标注 / 未找到就说未找到**。
