# 文档问答规则（多书语料库 · 工作区制）

本仓库是 **PDF → Markdown 语料库 + 索引**，按「工作区」隔离成互不相干的批次，
供 AI 辅助阅读与问答。

## 工作区机制（先理解这个）

```
仓库根/
├── AGENTS.md            通用问答规则 + 页码规范 (本文件)
├── add_book.sh          导入入口 (第一个参数 = 工作区名)
├── lib/                 共享工具 (pdf2md.py / search.py)
└── workspaces/
    ├── csg/             一批互不相干的书
    │   ├── AGENTS.md    该区的范围约束 + 书目路由 (AI 启动时读到的就是它)
    │   ├── catalog.md   该区书目 (本地生成, 不入库)
    │   └── books/<slug>/{source.pdf, index.json, AGENTS.md, md/}
    └── <另一个工作区>/   结构相同, 与前者毫无关联
```

- **不同工作区的书互不引用、互不相干**，视为独立项目。
- **AI 只处理当前工作区**：`cwd` 在 `workspaces/<name>/` 下时，只读取、引用该目录内的
  文件；其他 `workspaces/*` 一律不读取、不提及、不作为上下文。
- 在仓库根执行时，靠显式参数定范围：检索加 `-w <name>`，导入写
  `./add_book.sh <name> <pdf>`。

## 目录结构（工作区内）

```
workspaces/<name>/
├── AGENTS.md         该区范围约束 + 书目路由
├── catalog.md        该区书目 (书名/主题/页数/入口) —— 本地生成, 未入库
└── books/<slug>/
    ├── source.pdf    原始 PDF
    ├── index.json    元数据 + 章节树 + 页码三元映射 (page_map)
    ├── AGENTS.md     该书入口与已知缺陷
    └── md/
        ├── page_XXXX.md  逐页 Markdown (4 位页号 = PDF 页序, 1-based)
        ├── _toc.md       章节目录 (可读版, 含页码与链接)
        └── images/       页面插图
```

## 定位流程（必须按此顺序）

1. **选书**: 查本工作区 `catalog.md`，按主题确定 `slug`
   - 该文件是**本地文件**（导入时自动创建，不随仓库分发）；
     不存在时改用 `ls books/` 列出 slug，再读各 `books/<slug>/AGENTS.md` 的入口表选书
2. **定位章节**: 读 `books/<slug>/md/_toc.md`（或 `books/<slug>/index.json` 的 `chapters`）
   - 不要靠页码猜，书签是权威
3. **读内容**: 打开 `books/<slug>/md/page_XXXX.md`
4. **同名消歧**: 多本书都可能有 `Introduction`、`Patterns` 等章节，引用时**必须带 slug**

不知道在哪个书/页时，先跨书检索（**只搜本工作区**）再回到上面流程：

```bash
# 在工作区内执行
python3 ../../lib/search.py "PathExpander"
python3 ../../lib/search.py "MERGE" -n 5            # -r 正则; --json 供程序调用

# 在仓库根执行时, 用 -w 限定
python3 lib/search.py "PathExpander" -w csg
```

命中行会给出 `workspaces/<ws>/books/<slug>/md/page_XXXX.md`、印刷页、行号与所属章节，
直接按引用格式采用。

## 页码规范

三种页码并存，含义不同：

| 名称 | 含义 | 来源 |
|---|---|---|
| `page_XXXX.md` | **PDF 页序**（1-based，4 位补零） | 文件名 |
| `printed` | PDF 印刷页码（前言为 i, ii, iii…） | `index.json` → `page_map` |
| 章节页码 | 书签声明的起始页（= PDF 页序） | `index.json` → `chapters` |

引用格式：

```
(来源: workspaces/csg/books/apoc/md/page_0042.md, 印刷 p.20, §Configuration Options)
```

需要印刷页码或任意页映射时，查 `books/<slug>/index.json` 的 `page_map`（形如
`{"pdf": 42, "printed": "20", "md": "md/page_0042.md"}`）。

## 回答规则

1. **严格基于文档内容**：所有回答必须严格来源于本工作区 `books/*/md/` 下的 Markdown 内容，不得编造或推测。
2. **个人观点必须明确标注**：如需表达个人观点、经验总结或建议，必须明确标注，例如：
   - "根据我的理解……"
   - "结合实践经验……"
   - "我认为……"
3. **未标注即为文档内容**：在没有明确标注为个人观点的情况下，所回答的所有内容均应视为文档原文内容。
4. **引用需可追溯**：给出 `books/<slug>/md/page_XXXX.md`（含工作区），涉及章节时附章节名。
5. **未找到就说未找到**：对于不确定的内容，明确告知"文档中未找到相关信息"，而非自行推测。

## 已知局限（回答时须知）

- **图内文字**：示意图、查询计划图中的文字会被提取为普通正文，可能与真实段落混排
  （如 `16 db hits`、`:ACTED_IN` 这类标签；已排除其被误判为标题，但仍会出现在正文中）。
- **表格断词**：PDF 单元格内换行会被替换为空格，个别被拆断的单词可能残留空格
  （如 `from_fi le_urls`）。
- **不支持扫描件**：纯图片 PDF 会因提取不到文本而失败，需先 OCR。
- **无跨页段落合并**：一个段落跨越两页时会拆成两个文件，各自独立。

## 新增书籍 / 新建工作区

```bash
pip install -r requirements.txt            # 首次 (仅 pymupdf)

./add_book.sh <workspace> One-Book.pdf     # 区不存在 -> 自动创建
./add_book.sh <workspace> a.pdf b.pdf      # 区已存在 -> 追加
./add_book.sh <workspace> /path/manuals    # 一个目录 (递归)
./add_book.sh <workspace> book.pdf --slug my-slug
```

一条命令即产出 `books/<slug>/md/`、`index.json`、书级 `AGENTS.md` 骨架，
登记该区 `catalog.md` 与该区 `AGENTS.md` 的书目路由表，无需手工准备。
建议手工补充的只有一处：该书 `AGENTS.md` 的「已知缺陷 / 注意」段。
