"""读取标准数据集资源包，统一包描述、任务索引和包内路径规则。"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


@dataclass(frozen=True)
class DatasetPackage:
    root: Path
    metadata: dict
    index: dict
    dataset_sha256: str
    index_sha256: str

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def identity(self) -> dict:
        """返回供实验快照和续跑校验使用的资源版本。"""
        return {
            **{key: self.metadata[key] for key in ("id", "version", "task_format")},
            "dataset_sha256": self.dataset_sha256,
            "index_sha256": self.index_sha256,
        }


def _read_object(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"无法读取资源包文件：{path}：{exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"资源包文件必须为 JSON 对象：{path}")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError(f"资源包文件需要 schema_version=1：{path}")
    return document, hashlib.sha256(raw).hexdigest()


def read_package(root: str | Path, *, task_format: str | None = None) -> DatasetPackage:
    """读取根目录下的两个固定入口；此时不要求大型任务资源已经下载。"""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"资源包路径必须为目录：{root}")
    for name in ("dataset.json", "index.json"):
        resource_path(root, name)
    metadata, dataset_hash = _read_object(root / "dataset.json")
    for key in ("id", "version", "task_format"):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"dataset.json 的 {key} 必须为非空字符串：{root}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", metadata["id"]):
        raise ValueError(f"数据集 ID 无效：{metadata['id']!r}")
    if task_format is not None and metadata["task_format"] != task_format:
        raise ValueError(
            f"数据集格式 {metadata['task_format']!r} 与当前适配器需要的 {task_format!r} 不一致"
        )
    index, index_hash = _read_object(root / "index.json")
    tasks = index.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"index.json 的 tasks 必须为非空任务列表：{root}")
    ids = set()
    for task in tasks:
        task_id = task.get("id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
            raise ValueError(f"索引中的任务 ID 无效：{task_id!r}")
        if task_id in ids:
            raise ValueError(f"索引中的任务 ID 重复：{task_id!r}")
        ids.add(task_id)
    if "task_count" in index and (
        type(index["task_count"]) is not int or index["task_count"] != len(tasks)
    ):
        raise ValueError(f"index.json 的 task_count 与任务列表不一致：{root}")
    return DatasetPackage(root, metadata, index, dataset_hash, index_hash)


def read_package_index(index: str | Path, *, task_format: str) -> DatasetPackage:
    """供现有 load(index, ids) 和任务准备接口读取所属资源包。"""
    index = Path(index)
    if index.name != "index.json":
        raise ValueError(f"标准资源包的任务入口必须为 index.json：{index}")
    return read_package(index.parent, task_format=task_format)


def resource_path(root: Path, value: str, *, must_exist: bool = True) -> Path:
    """解析包内的 POSIX 相对路径，拒绝越界路径及链接资源。"""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"资源路径必须为使用 / 分隔的包内相对路径：{value!r}")
    relative = PurePosixPath(value)
    if (relative.is_absolute() or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(character in value for character in '\x00:<>"|?*')):
        raise ValueError(f"资源路径必须限定在包内：{value!r}")
    root = Path(root).resolve(strict=True)
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"不支持链接形式的资源：{path}")
    resolved = path.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise ValueError(f"资源路径超出包目录：{value!r}")
    return resolved
