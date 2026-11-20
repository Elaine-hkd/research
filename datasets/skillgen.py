"""从仓库的文档与源码生成可复用技能（SKILL.md）。

生成器只读 `code_base/<Repo>`：README、文档、示例清单与公开 API 摘要。
真值（groundtruth）与评分脚本（test_script）不参与生成，也不允许出现在产物里，
否则技能就变成「抄答案」。

两种模式：
- `template`：完全离线、确定性输出，用于跑通链路与做基线对照；
- `llm`：把收集到的材料交给模型，产出面向任务的操作说明（默认）。

用法：

    python -m datasets.skillgen \
        --repo data/gittaskbench/code_base/PDFPlumber \
        --out data/gittaskbench/skills/PDFPlumber \
        --commit df188a73013393d036442bfb0f323c4c995fa5e1
"""

import argparse
import ast
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from utils.json_io import write_json


README_LIMIT = 12000
DOC_LIMIT = 2500
DOC_FILES = 3
MODULE_LIMIT = 12
SYMBOL_LIMIT = 60
BODY_MIN_CHARS = 300
MAX_OUTPUT_TOKENS = 8000

_SKIP_DIRS = {
    ".git", "__pycache__", "build", "dist", "node_modules", "tests", "test",
    "docs", "docs_src", ".tox", ".mypy_cache", "site-packages",
}
# 技能里出现这些词，说明生成时看到了评分材料，必须判失败。
_FORBIDDEN = ("groundtruth", "test_script", "gt.csv", "gt.txt", "verifier/")


class GenerationError(RuntimeError):
    """生成结果不满足技能契约。"""


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…（已截断）"


def _signature(node) -> str:
    """把函数参数列表还原成可读签名。"""
    args = node.args
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        text = arg.arg
        if index >= offset:
            text += "=" + _safe_unparse(args.defaults[index - offset])
        parts.append(text)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if default is not None:
            text += "=" + _safe_unparse(default)
        parts.append(text)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _safe_unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - 签名展示失败不影响生成
        return "…"


def _summary(node) -> str:
    doc = ast.get_docstring(node) or ""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def collect(repo: Path) -> dict:
    """只从仓库里收集材料；不做任何网络访问。"""
    repo = repo.resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"仓库目录不存在：{repo}")

    readme_path = next((repo / name for name in ("README.md", "README.rst", "readme.md")
                        if (repo / name).is_file()), None)
    readme = _truncate(readme_path.read_text(encoding="utf-8-sig"), README_LIMIT) if readme_path else ""

    docs = []
    for path in sorted((repo / "docs").rglob("*.md"))[:DOC_FILES] if (repo / "docs").is_dir() else []:
        docs.append({"path": path.relative_to(repo).as_posix(),
                     "text": _truncate(path.read_text(encoding="utf-8-sig", errors="replace"), DOC_LIMIT)})

    examples = [path.relative_to(repo).as_posix()
                for path in sorted(repo.rglob("*.py"))
                if "example" in path.parts[-3:] or "example" in path.name.lower()][:10]

    symbols, modules = [], 0
    for path in sorted(repo.rglob("*.py"), key=lambda item: (len(item.parts), item.as_posix())):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo).parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            continue
        entries = []
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                entries.append({"kind": "函数", "name": node.name,
                                "signature": _signature(node), "summary": _summary(node)})
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                entries.append({"kind": "类", "name": node.name,
                                "signature": "", "summary": _summary(node)})
        if not entries:
            continue
        modules += 1
        for entry in entries:
            entry["module"] = path.relative_to(repo).as_posix()
            symbols.append(entry)
        if modules >= MODULE_LIMIT or len(symbols) >= SYMBOL_LIMIT:
            break

    files = []
    for path in (readme_path, *[repo / doc["path"] for doc in docs]):
        if path is not None:
            files.append({"path": path.relative_to(repo).as_posix(),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    return {
        "repo": repo,
        "name": repo.name,
        "readme": readme,
        "readme_path": readme_path.relative_to(repo).as_posix() if readme_path else None,
        "docs": docs,
        "examples": examples,
        "symbols": symbols[:SYMBOL_LIMIT],
        "modules": modules,
        "files": files,
    }


def _material(material: dict) -> str:
    """把材料压成一份可读的清单，供模型或模板使用。"""
    lines = [f"仓库名：{material['name']}",
             f"README：{material['readme_path'] or '（无）'}",
             f"扫描到的模块数：{material['modules']}，公开符号数：{len(material['symbols'])}", ""]
    if material["readme"]:
        lines += ["## README 节选", material["readme"], ""]
    for doc in material["docs"]:
        lines += [f"## 文档 {doc['path']}", doc["text"], ""]
    if material["examples"]:
        lines += ["## 示例文件", *[f"- {path}" for path in material["examples"]], ""]
    if material["symbols"]:
        lines += ["## 公开 API 摘要"]
        for item in material["symbols"]:
            signature = item["signature"] if item["kind"] == "函数" else ""
            lines.append(f"- [{item['kind']}] `{item['module']}::{item['name']}{signature}`"
                         + (f" —— {item['summary']}" if item["summary"] else ""))
    return "\n".join(lines).strip()


_PROMPT = """你在为一个自动评测任务编写可复用的操作技能。

下面是一个真实开源仓库的文档与源码摘要。请写一份 `SKILL.md`，让另一个 agent 在只拿到这个仓库副本的情况下，
能照着它完成「读文档 → 用仓库已有能力产出指定文件」的交付。

硬性要求：
1. 第一行必须是 `---`，紧接着 YAML 前置元数据，包含两个字段：`name`（小写、用连字符）与 `description`（一句话说明适用场景）；再用一行 `---` 收尾。
2. 正文用二级标题组织，至少包含：适用场景、安装与导入、核心 API（用上表里出现的真实签名）、最小可运行示例、常见坑、不适用范围。
3. 只写这份材料里能验证的内容；参数名、函数名必须来自材料，不要编造。
4. 不要提到任何测试、真值、评分脚本或答案文件——技能只描述怎么用仓库，不描述怎么判分。
5. **运行前提**：仓库副本就在任务目录里，并且已经用 `pip install -e` 装好了。
   「安装与导入」一节只写怎么 `import`、以及材料里明确要求的系统依赖；
   **不要**写 `pip install <包名>`——从 PyPI 安装同名包会覆盖仓库源码，必须避免。
6. 代码示例使用 Windows 上也能运行的写法：解释器写 `python`，路径用相对路径，示例文件名用占位名（如 `input.pdf`），不要写死绝对路径。
7. 全文中文，控制在 900 字以内。

只输出 SKILL.md 的内容本身，不要加额外的解释或代码块围栏。

===== 材料开始 =====
{material}
===== 材料结束 =====
"""


def _api_key(name: str) -> str:
    """优先读环境变量，其次读项目根目录的 .env（本机实验环境用后者）。"""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == name:
                return raw.strip().strip('"').strip("'")
    raise ValueError(f"缺少模型密钥：环境变量与 .env 中都没有 {name}")


def _front_matter_text(metadata: dict) -> str:
    """用 YAML 安全序列化前置元数据：README 里的徽章、冒号不会破坏语法。"""
    import yaml

    body = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).strip()
    return "---\n" + body + "\n---"


def render_template(material: dict) -> str:
    """离线模式：按固定结构拼一份保守的技能，用于跑通链路或做对照。"""
    name = re.sub(r"[^a-z0-9]+", "-", material["name"].lower()).strip("-") or "repo"
    paragraphs = [block.strip() for block in material["readme"].split("\n\n")]
    first_paragraph = next((block for block in paragraphs
                            if block and not block.lstrip().startswith(("#", "-", "|", "!", "["))
                            and "](" not in block),
                           f"{material['name']} 仓库的使用说明。")
    description = re.sub(r"\s+", " ", first_paragraph)[:180]
    front = _front_matter_text({"name": name, "description": description})
    lines = [front, "",
             f"# {material['name']} 操作技能", "",
             "## 适用场景", "",
             description, "",
             "## 安装与导入", "",
             "仓库已在任务目录内，并已用 `pip install -e` 安装；直接 `import` 即可使用，"
             "不要从 PyPI 安装同名包覆盖它。", ""]
    if material["symbols"]:
        lines += ["## 核心 API", ""]
        for item in material["symbols"][:20]:
            signature = item["signature"] if item["kind"] == "函数" else ""
            summary = f" —— {item['summary']}" if item["summary"] else ""
            lines.append(f"- `{item['name']}{signature}`（{item['module']}）{summary}")
        lines.append("")
    lines += ["## 最小可运行示例", "",
              "```python",
              f"import {Path(material['symbols'][0]['module']).stem if material['symbols'] else 'package'}",
              "```", "",
              "## 常见坑", "",
              "- 仓库是任务本体：先读 README 与相关源码再动手，不要另写一套实现。",
              "- 产物路径以任务指令为准，文件名和扩展名必须完全一致。", "",
              "## 不适用范围", "",
              "- 本技能只覆盖仓库文档里明确描述的能力；仓库未提供的功能不要臆造。", ""]
    return "\n".join(lines)


def _llm(material: dict, *, model: str, api_base: str, key_env: str) -> str:
    from research_llm import completion, configure

    configure(project_id="gittaskbench-skillgen",
              ledger_path=str(Path(__file__).resolve().parents[1] / "logs" / "model-usage.sqlite3"))
    response = completion(
        model=model,
        messages=[{"role": "user", "content": _PROMPT.format(material=_material(material))}],
        api_key=_api_key(key_env),
        api_base=api_base,
        temperature=0.2,
        # 推理型模型先输出 reasoning_content，预算太小会只剩思考、没有正文。
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    message = response.choices[0].message
    content = (message.content or "").strip()
    if not content:
        reasoning = getattr(message, "reasoning_content", None) or ""
        raise GenerationError(
            f"模型没有输出正文（finish_reason={response.choices[0].finish_reason}，"
            f"思考 {len(reasoning)} 字符）；请调大 max_tokens 或换非推理模型")
    return content


def validate(text: str, *, repo_name: str) -> str:
    """校验并规范化生成结果；不合格直接报错，不写半成品技能。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text).strip()
    if not text.startswith("---"):
        raise GenerationError("技能缺少 YAML 前置元数据")
    lowered = text.lower()
    leaked = [word for word in _FORBIDDEN if word in lowered]
    if leaked:
        raise GenerationError(f"技能里出现了评分材料相关字样：{leaked}")
    if re.search(r"[A-Za-z]:\\\\|[A-Za-z]:/Users|/home/", text):
        raise GenerationError("技能里出现了本机绝对路径")
    # README 常写 `pip install <包名>`；照抄会让 agent 用 PyPI 包覆盖仓库源码。
    for match in re.finditer(r"pip3?\s+install\s+([^\s`]+)", lowered):
        target = match.group(1)
        if not target.startswith(("-", ".")):
            raise GenerationError(
                f"技能指示从 PyPI 安装 {target}，会覆盖任务目录里的仓库源码；"
                "应改为使用已 `pip install -e` 装好的副本")

    lines = text.splitlines()
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise GenerationError("YAML 前置元数据未闭合")
    import yaml

    metadata = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise GenerationError("前置元数据必须是映射")
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        raise GenerationError(f"技能 name 必须为小写标识：{name!r}")
    if not description:
        raise GenerationError("技能 description 不能为空")
    body = "\n".join(lines[end + 1:]).strip()
    if len(body) < BODY_MIN_CHARS:
        raise GenerationError(f"技能正文过短（{len(body)} 字符），至少 {BODY_MIN_CHARS}")
    # 统一用 YAML 序列化重写前置元数据，保证下游用 safe_load 一定能解析。
    canonical = dict(metadata)
    canonical["name"] = name
    canonical["description"] = description
    return _front_matter_text(canonical) + "\n\n" + body + "\n"


def generate(repo: Path, out: Path, *, mode: str, model: str, api_base: str,
             key_env: str, commit: str | None) -> dict:
    material = collect(repo)
    text = render_template(material) if mode == "template" else _llm(
        material, model=model, api_base=api_base, key_env=key_env)
    text = validate(text, repo_name=material["name"])

    out.mkdir(parents=True, exist_ok=True)
    (out / "SKILL.md").write_text(text, encoding="utf-8")
    provenance = {
        "repo": material["name"],
        "commit": commit,
        "mode": mode,
        "model": model if mode == "llm" else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": material["files"],
        "modules_scanned": material["modules"],
        "symbols": len(material["symbols"]),
        "skill_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "skill_bytes": len(text.encode("utf-8")),
    }
    write_json(out / "provenance.json", provenance)
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从仓库文档与源码生成 SKILL.md")
    parser.add_argument("--repo", required=True, type=Path, help="仓库目录（包内 code_base/<Repo>）")
    parser.add_argument("--out", required=True, type=Path, help="技能输出目录")
    parser.add_argument("--mode", choices=("llm", "template"), default="llm")
    parser.add_argument("--model", default="deepseek/deepseek-flash")
    parser.add_argument("--api-base", default="https://uniapi.thingo.com.cn/v1")
    parser.add_argument("--api-key-env", default="THINGO_UNI_API_KEY")
    parser.add_argument("--commit", default=None, help="上游 commit，写入 provenance")
    args = parser.parse_args(argv)

    try:
        provenance = generate(args.repo, args.out, mode=args.mode, model=args.model,
                              api_base=args.api_base, key_env=args.api_key_env,
                              commit=args.commit)
    except (GenerationError, ValueError, FileNotFoundError) as error:
        print(f"生成失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
