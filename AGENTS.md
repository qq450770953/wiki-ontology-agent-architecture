# AGENTS.md — 面向 AI 会话的仓库操作规范

> 用途：任何 AI 助手（或人）在本仓库工作时，先读本文件。它把本仓库的**隐含约定与可复用工作流**显式化，避免每次重新摸索。
> 版本：v1（2026-09-03，随 architecture.md v1.5 加入）

## 1. 仓库性质

- 本仓库是**架构设计文档仓**，不是代码仓。核心交付物是 `docs/architecture.md`（当前 v1.5）+ 两张架构图（SVG）+ README。
- 可运行参考实现是独立的 [ontology-enterprise](https://github.com/qq450770953/ontology-enterprise)（Python CLI + SQLite），**不要**在本仓库写实现代码；确需实验时放 `.workbuddy/tmp_*` 并在完成后删除。

## 2. 文档约定

- `docs/architecture.md` 头部有版本块：`> 版本：vX.Y（日期）`。**每次实质修订必须升版本号并在版本块下方写"增量说明"**，引用外部来源时给出处章节。
- 文档含**等宽 ASCII 架构图**（``` 代码块内）。这些行是字符对齐的：**编辑时不要 `strip()` 行首/行尾空格，不要按视觉列数重排**；新增行若需插入，先数清相邻行的字符数与缩进（内容行两空格、层标题一空格），保持一致，改完用只读方式目测一遍。
- 中文字符在 `architecture.md` 中的对齐以**字符计数**为准（CJK 宽字符也按 1 计数 pad），不要用 `str.ljust` 或按字节数 pad。
- 全文用中文撰写；术语首次出现给出英文（如 上下文物化 Context Materialization）。

## 3. 外部来源与引用

- `docs/references/` 只放**已获授权或作者自产**的参考资料副本（当前仅 1 份 Actionable_Knowledge_Architecture.pdf 扫描件）。大体积外部 PDF（如《智能体 AI 权威指南》19.9MB）**不入库**，只在 `architecture.md` §12 记录书目信息与出处章节。
- 参考书结论落地时保持"可溯源"：正文写结论，§12 标注来源（书章节号），不要只给感想。

## 4. 隐含工作流（AI 会话经常用到）

以下为本机已验证的命令模式，来自历史会话：

### 4.1 长 PDF 抽取 / 探测（文字版 or 扫描件）

```bash
PY="C:/Users/Administrator/.workbuddy/skills/pdfkit-py/scripts/venv/Scripts/python.exe"
"$PY" -c "import pymupdf; d=pymupdf.open(r'<pdf路径>'); print(d.page_count)"
```

- 用 `python -c` 或写 `.workbuddy/tmp_*.py`（用后即删）调用 pymupdf 抽取文本。
- 先探测每页字符数判断是否扫描件（字符 <50 且含图片 → 扫描件，需 OCR，不要假装能直接读）。
- 临时抽取的大文本放 `.workbuddy/`，用完删除；**不提交**。

### 4.2 SVG → PNG（本机 Chrome headless；cairosvg 不可用）

```bash
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe"
"$CHROME" --headless=new --disable-gpu --screenshot="<out>.png" \
  --default-background-color=FFFFFFFF --window-size=1360,<高> "file:///C:/.../<svg 绝对路径>"
```

### 4.3 Markdown/Word 导出（python-docx）

- 用 python-docx 生成 .docx 时：中文字体需同时设置 `style.font.name` 与 `element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')`。
- **写脚本用 bash heredoc**（`cat > 文件 << 'PYEOF'`）确保内容持久化；写完执行、成功即删。

### 4.4 ima 知识库上传（个人版）

- 上传流程：`create_media` → COS v1 签名 PUT → `add_knowledge`。
- **COS PUT host 用标准域名 `bucket-appid.cos.<region>.myqcloud.com`，不要用 custom_domain**（历史踩坑）。
- 知识库 ID：`001a6fd997006efe`（"艾斯的知识库"）。
- 密钥等敏感值走临时注入，**不落盘、不展示**。

## 5. 文件治理

- `.workbuddy/` 是本机记忆与临时目录：**永不 git add**（.gitignore 已排除）；项目记忆、交接文档放 `.workbuddy/memory/`。
- `docs/references/`、`docs/images/` 的改动要同步更新 README 与 architecture.md 中的引用，避免死链。
- 提交前检查：`git status --short` 应只出现预期文件；提交消息用一行概述 + 版本号（如 `docs: v1.5 运行时护栏落地（ContextPack/四层评估/分级授权）`）。

## 6. 验证清单（声称"完成"前）

1. `architecture.md` 版本块已升版并写增量说明；改动处可检索到（`grep -c "关键词"`）。
2. 修改过的 SVG 通过 XML 解析校验（`xml.etree.ElementTree.parse` 无异常）。
3. ASCII 架构图行未因编辑而错位（新行与相邻行字符数一致）。
4. README 引用无死链；新文档（如本文件）已加入 README 仓库结构。
5. `.workbuddy/` 下无本次遗留的 `tmp_*` 脚本。
