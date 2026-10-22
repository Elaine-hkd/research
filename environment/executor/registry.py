"""遵循现有技能注册中心 HTTP 协议的只读客户端。"""

import json
from pathlib import PurePosixPath, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .base import ExecutionError, checked_timeout


def resource_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError("注册中心资源路径必须为非空字符串")
    path = value.replace("\\", "/")
    parts = path.split("/")
    if (PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
            or any(p in {"", ".", ".."} for p in parts)
            or any(any(c in p for c in '\x00:<>"|?*') or p.endswith((".", " ")) for p in parts)
            or any(PureWindowsPath(p).is_reserved() for p in parts)):
        raise ExecutionError(f"不安全的注册中心资源路径：{value!r}")
    return path


class RegistryClient:
    def __init__(self, registry_url, *, timeout=30, allowed_skills=None):
        parsed = urlsplit(registry_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("registry_url 必须为不含查询参数或片段的 HTTP(S) 基础 URL")
        self.url = registry_url.rstrip("/")
        self.timeout = checked_timeout(timeout)
        if isinstance(allowed_skills, str):
            raise ValueError("allowed_skills 必须为技能名称集合")
        self.allowed = None if allowed_skills is None else frozenset(allowed_skills)

    def request(self, endpoint, params):
        """返回原始 JSON 响应结构，并执行客户端的技能范围限制。"""
        params = dict(params)
        if endpoint == "/search_skills":
            if self.allowed is not None:
                # 部分注册中心将 [] 视为不筛选，因此在本地强制返回空结果。
                if not self.allowed:
                    return {"count": 0, "skills": []}
                params["exposed_skills"] = json.dumps(sorted(self.allowed))
        elif endpoint in {"/load_skill", "/load_resource"}:
            skill_name = params.get("skill_name")
            if not isinstance(skill_name, str) or not skill_name.strip():
                raise ValueError("skill_name 必须为非空字符串")
            if self.allowed is not None and skill_name not in self.allowed:
                raise ValueError(f"未知或未获准访问的技能：{skill_name!r}")
        else:
            raise ValueError(f"不支持的注册中心读取端点：{endpoint!r}")
        url = self.url + endpoint + "?" + urlencode(params)
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ExecutionError(f"注册中心 {endpoint} HTTP {exc.code}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise ExecutionError(f"注册中心 {endpoint} 请求失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ExecutionError(f"注册中心 {endpoint} 必须返回 JSON 对象")
        if endpoint == "/search_skills" and self.allowed is not None:
            skills = data.get("skills")
            if not isinstance(skills, list):
                raise ExecutionError("/search_skills 响应无效")
            skills = [skill for skill in skills
                      if (skill.get("name") if isinstance(skill, dict) else skill) in self.allowed]
            data = {**data, "skills": skills, "count": len(skills)}
        return data

    def _get(self, endpoint, skill_name, **params):
        return self.request(endpoint, {"skill_name": skill_name, **params})

    def load_skill(self, skill_name):
        skill = self._get("/load_skill", skill_name).get("skill")
        if (not isinstance(skill, dict) or skill.get("name") != skill_name
                or not isinstance(skill.get("content"), str)
                or not isinstance(skill.get("resources"), list)):
            raise ExecutionError("/load_skill 响应无效")
        return skill

    def load_resource(self, skill_name, relative_path):
        path = resource_path(relative_path)
        data = self._get("/load_resource", skill_name, relative_path=relative_path)
        if data.get("success") is False:
            error = data.get("error")
            if (not isinstance(error, dict)
                    or not isinstance(error.get("code"), str)
                    or not isinstance(error.get("message"), str)):
                raise ExecutionError("/load_resource 错误响应无效")
            raise ExecutionError(
                f"注册中心 /load_resource [{error['code']}]: {error['message']}"
            )
        if (data.get("success") is not True
                or data.get("error") is not None
                or data.get("skill_name") != skill_name
                or resource_path(data.get("relative_path")) != path
                or not isinstance(data.get("content"), str)):
            raise ExecutionError("/load_resource 响应无效")
        return data["content"]

    def fetch_files(self, skill_name, entrypoint):
        skill = self.load_skill(skill_name)
        # 注册中心提供指令正文，不包含原始 YAML 头部。
        files = {"SKILL.md": skill["content"]}
        seen = {"skill.md"}
        paths = []
        for original in skill["resources"]:
            path = resource_path(original)
            if path.casefold() in seen:
                raise ExecutionError(f"重复的注册中心资源路径：{original!r}")
            seen.add(path.casefold())
            paths.append((original, path))
        if resource_path(entrypoint) not in {p for _, p in paths}:
            raise ExecutionError(f"注册中心未列出该资源：{entrypoint!r}")
        for original, path in paths:
            try:
                files[path] = self.load_resource(skill_name, original)
            except ExecutionError as exc:
                raise ExecutionError(
                    f"无法下载 {skill_name}/{path}。此 API 仅支持 UTF-8 文本。{exc}"
                ) from exc
        return files
