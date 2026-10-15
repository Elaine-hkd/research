"""Reconstruct a native task package from the existing split research index."""

import json
from pathlib import Path
import shutil


def _native_task_text(source: Path) -> str:
    """Rename the legacy sandbox key in YAML; keep the Markdown body intact."""
    import yaml

    text = source.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i, line in enumerate(lines[1:], 1)
                if line.strip() == "---"), None)
    if end is None:
        raise ValueError(f"Task YAML frontmatter is not closed: {source}")
    config = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(config, dict):
        raise ValueError(f"Task YAML frontmatter must be a mapping: {source}")
    if "environment" not in config:
        return text
    if "sandbox" in config:
        raise ValueError(f"Task declares both environment and sandbox: {source}")
    config["sandbox"] = config.pop("environment")
    header = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    return "---\n" + header + "---\n" + "".join(lines[end + 1:])


def materialize_skillsbench_task(index_path: str | Path, task_id: str,
                                destination: str | Path) -> Path:
    """Copy task data to a new persistent staging directory; never edit originals.

    Caller owns the staging directory. No environment is started here.
    Absolute index paths are preserved; relative paths resolve beside the index.
    Only the staged task.md renames environment to sandbox for BenchFlow 0.7.8.
    """
    index_path = Path(index_path).resolve(strict=True)
    rows = json.loads(index_path.read_text(encoding="utf-8-sig"))["tasks"]
    matches = [row for row in rows if row["id"] == task_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one task named {task_id!r}, found {len(matches)}")
    row = matches[0]
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"Staging destination already exists: {destination}")
    entries = [(row["task_file"], "task.md"),
               (row["environment_dir"], "environment"),
               (row["verifiler_dir"], "verifier"),
               (row["oracle_dir"], "oracle")]
    entries.extend((path, Path(path).name) for path in row.get("extra_paths", []))
    names = set()
    checked = []
    for path, name in entries:
        if name.casefold() in names or name in {"", ".", ".."}:
            raise ValueError(f"Conflicting staged path: {name}")
        names.add(name.casefold())
        source = Path(path)
        source = source if source.is_absolute() else index_path.parent / source
        if source.is_symlink() or source.is_junction():
            raise ValueError(f"Linked task resources are unsupported: {source}")
        source = source.resolve(strict=True)
        if source == destination or source in destination.parents or destination in source.parents:
            raise ValueError("Staging destination overlaps task sources")
        if source.is_dir():
            for item in source.rglob("*"):
                if item.is_symlink() or item.is_junction():
                    raise ValueError(f"Linked task resources are unsupported: {item}")
        checked.append((source, name))
    task_text = _native_task_text(checked[0][0])
    destination.mkdir(parents=True)
    for source, name in checked:
        target = destination / name
        if name == "task.md":
            target.write_text(task_text, encoding="utf-8")
        elif source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return destination
