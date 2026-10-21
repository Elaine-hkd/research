"""Reconstruct a native task package from the existing split research index."""

import json
from pathlib import Path
import re
import shutil


_PIP_MIRROR_MARKER = "# research: pip mirror (TUNA)"
_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
_APT_MIRROR_MARKER = "# research: APT mirror (TUNA)"
_APT_MIRROR_COMMAND = r"""
if [ ! -f /etc/os-release ]; then
    echo 'research: APT mirror skipped (no /etc/os-release)'
    exit 0
fi
. /etc/os-release
case "$ID" in
    ubuntu|debian) ;;
    *) echo "research: APT mirror skipped ($ID)"; exit 0 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
    echo 'research: APT mirror requires root before the first RUN' >&2
    exit 1
fi
# HTTP also works in minimal images without ca-certificates; APT signatures stay enabled.
for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -f "$source" ] || continue
    sed -E -i \
        -e 's@https?://([[:alnum:]-]+\.)?(archive|security)\.ubuntu\.com/ubuntu(/|[[:space:]]|$)@http://mirrors.tuna.tsinghua.edu.cn/ubuntu\3@g' \
        -e 's@https?://ports\.ubuntu\.com/ubuntu-ports(/|[[:space:]]|$)@http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports\1@g' \
        -e 's@https?://(deb|security)\.debian\.org/debian-security(/|[[:space:]]|$)@http://mirrors.tuna.tsinghua.edu.cn/debian-security\2@g' \
        -e 's@https?://security\.debian\.org/debian(/|[[:space:]]|$)@http://mirrors.tuna.tsinghua.edu.cn/debian-security\1@g' \
        -e 's@https?://(deb\.debian\.org|ftp\.([[:alnum:]-]+\.)?debian\.org)/debian(/|[[:space:]]|$)@http://mirrors.tuna.tsinghua.edu.cn/debian\3@g' \
        "$source"
done
echo "research: APT mirror configured for $ID (TUNA)"
""".strip()


def _inject_package_mirrors(dockerfile: Path) -> None:
    """Set pip after FROM and prepare APT before each stage's first RUN."""
    text = dockerfile.read_text(encoding="utf-8-sig")
    inject_pip = _PIP_MIRROR_MARKER not in text
    inject_apt = _APT_MIRROR_MARKER not in text
    if not inject_pip and not inject_apt:
        return
    lines = text.splitlines(keepends=True)
    pip_block = f"\n{_PIP_MIRROR_MARKER}\nENV PIP_INDEX_URL={_PIP_INDEX_URL}\n"
    apt_block = f"\n{_APT_MIRROR_MARKER}\nRUN " + json.dumps(
        ["/bin/sh", "-ec", _APT_MIRROR_COMMAND],
    ) + "\n"
    insertions = {}
    pending_stage = False
    escape = "\\"
    i = 0
    while i < len(lines):
        start = i
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            directive = re.fullmatch(r"\s*#\s*escape\s*=\s*([\\`])\s*", line)
            if directive:
                escape = directive[1]
            continue
        # Consume a whole instruction so continuation lines are never instructions.
        while line.rstrip("\r\n").endswith(escape) and i < len(lines):
            line = lines[i]
            i += 1
            while line.lstrip().startswith("#") and i < len(lines):
                line = lines[i]
                i += 1
        instruction = re.match(r"\s*([a-z]+)\s+(.*)", lines[start], re.IGNORECASE)
        if instruction is None:
            continue
        keyword = instruction[1].upper()
        if keyword == "FROM":
            arguments = "".join(lines[start:i]).replace(escape + "\n", " ").split()[1:]
            image = next(part for part in arguments if not part.startswith("--"))
            pending_stage = image.lower() != "scratch" and inject_apt
            if image.lower() != "scratch" and inject_pip:
                insertions[i] = pip_block
        elif keyword == "RUN" and pending_stage:
            insertions[start] = insertions.get(start, "") + apt_block
            pending_stage = False
        # A heredoc may contain FROM/RUN as data, including embedded Python imports.
        if keyword in {"RUN", "COPY"}:
            command = "".join(lines[start:i])
            for heredoc in re.finditer(r"<<(-?)[ \t]*(?:'([^']+)'|\"([^\"]+)\"|([a-zA-Z_][a-zA-Z0-9_]*))", command):
                delimiter = next(value for value in heredoc.groups()[1:] if value is not None)
                while i < len(lines):
                    body = lines[i].rstrip("\r\n")
                    i += 1
                    if (body.lstrip("\t") if heredoc[1] else body) == delimiter:
                        break
                else:
                    raise ValueError(f"Unclosed Dockerfile heredoc {delimiter!r}: {dockerfile}")
    # A final stage without RUN can still receive the desktop installer afterwards.
    if pending_stage:
        insertions[len(lines)] = insertions.get(len(lines), "") + apt_block
    if insertions:
        rewritten = "".join(insertions.get(index, "") + line
                            for index, line in enumerate(lines))
        rewritten += insertions.get(len(lines), "")
        dockerfile.write_text(rewritten, encoding="utf-8", newline="\n")


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
    Staged Dockerfile* files use TUNA for pip and official Ubuntu/Debian APT sources.
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
    for dockerfile in sorted((destination / "environment").rglob("Dockerfile*")):
        if dockerfile.is_file():
            _inject_package_mirrors(dockerfile)
    return destination
