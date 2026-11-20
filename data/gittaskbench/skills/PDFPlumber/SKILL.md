---
name: pdfplumber-pdf-extraction
description: 用仓库内 pdfplumber 读取 PDF、提取文本/对象/结构并生成指定输出文件。
---

## 适用场景
仓库副本已在任务目录且已 `pip install -e` 后，用 `pdfplumber` 处理机器生成的 PDF：读元数据、逐页取字符/线条/矩形/图片、裁剪过滤页面、提取文本、修复畸形 PDF、读取结构树，并将结果写入指定文件。

## 安装与导入
不要从 PyPI 安装同名包；直接导入仓库源码：
```python
import pdfplumber
from pdfplumber.ctm import CTM
```
修复畸形 PDF 需系统 Ghostscript；自动检测失败时传 `gs_path`。

## 核心 API
- `pdfplumber.open(x)` 返回 `PDF`，可传 `password=...`、`laparams=...`、`unicode_norm=...`、`strict_metadata=True`。
- `PDF`：`.metadata`、`.pages`、`.close()`。
- `Page`：`.page_number`、`.width`、`.height`、`.objects`/`.chars`/`.lines`/`.rects`/`.curves`/`.images`；`.crop(bounding_box, relative=False, strict=True)`、`.within_bbox(bounding_box, relative=False, strict=True)`、`.outside_bbox(bounding_box, relative=False, strict=True)`、`.filter(test_function)`、`.close()`。
- 文本：`Page.extract_text(layout=True)`。
- 字符矩阵：`CTM(*char["matrix"]).skew_x`。
- 修复：`pdfplumber.repair(path_or_fp, outfile=None, password=None, gs_path=None, setting='default')`。
- 结构：`page.structure_tree`；元素含 `type`、`mcids`、`lang`、`alt_text`、`attributes`。
- 表格：公开符号含 `TableFinder`、`Table`、`TableSettings`，具体设置见 README 的 Extracting tables 节。

## 最小可运行示例
```python
import pdfplumber

with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]
    print(page.page_number, page.width, page.height)
    print(pdf.metadata)
    text = page.extract_text(layout=True) or ""
    with open("output.txt", "w", encoding="utf-8") as f:
        f.write(text)
```
在 Windows 当前目录放 `input.pdf`，运行 `python script.py`，生成相对路径 `output.txt`。

## 常见坑
- 扫描件效果差，优先用于机器生成的 PDF；密码 PDF 需传 `password`。
- `.crop` 保留部分相交对象；`.within_bbox` 只保留完全在内；`.outside_bbox` 只保留完全在外；`strict=True` 时裁剪框须在页面内。
- 大 PDF 用 `Page.close()` 释放缓存；`PDF.close()` 关闭页面与文件流。
- 颜色空间目前仅能可靠给出 `char` 的非描边颜色空间；结构树并非所有 PDF 都有。
- 畸形 PDF 可用 `pdfplumber.open(..., repair=True)` 或 `repair(...)`。

## 不适用范围
不用于扫描件 OCR、PDF 内容编辑/生成排版；无 Ghostscript 时不保证修复畸形 PDF；结构树缺失时不能据此恢复逻辑结构。
