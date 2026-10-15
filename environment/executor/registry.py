"""Read-only client for the existing skill registry HTTP contract."""

import json
from pathlib import PurePosixPath, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .base import ExecutionError, checked_timeout


def resource_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError("Registry resource path must be a non-empty string")
    path = value.replace("\\", "/")
    parts = path.split("/")
    if (PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
            or any(p in {"", ".", ".."} for p in parts)
            or any(any(c in p for c in '\x00:<>"|?*') or p.endswith((".", " ")) for p in parts)
            or any(PureWindowsPath(p).is_reserved() for p in parts)):
        raise ExecutionError(f"Unsafe registry resource path: {value!r}")
    return path


class RegistryClient:
    def __init__(self, registry_url, *, timeout=30, allowed_skills=None):
        parsed = urlsplit(registry_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("registry_url must be an HTTP(S) base URL without query or fragment")
        self.url = registry_url.rstrip("/")
        self.timeout = checked_timeout(timeout)
        if isinstance(allowed_skills, str):
            raise ValueError("allowed_skills must be a collection of skill names")
        self.allowed = None if allowed_skills is None else frozenset(allowed_skills)

    def _get(self, endpoint, skill_name, **params):
        if not isinstance(skill_name, str) or not skill_name.strip():
            raise ValueError("skill_name must be a non-empty string")
        if self.allowed is not None and skill_name not in self.allowed:
            raise ValueError(f"Unknown or disallowed skill: {skill_name!r}")
        url = self.url + endpoint + "?" + urlencode({"skill_name": skill_name, **params})
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ExecutionError(f"Registry {endpoint} HTTP {exc.code} for skill {skill_name!r}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise ExecutionError(f"Registry {endpoint} request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ExecutionError(f"Registry {endpoint} must return a JSON object")
        return data

    def load_skill(self, skill_name):
        skill = self._get("/load_skill", skill_name).get("skill")
        if (not isinstance(skill, dict) or skill.get("name") != skill_name
                or not isinstance(skill.get("content"), str)
                or not isinstance(skill.get("resources"), list)):
            raise ExecutionError("Invalid /load_skill response")
        return skill

    def load_resource(self, skill_name, relative_path):
        path = resource_path(relative_path)
        data = self._get("/load_resource", skill_name, relative_path=relative_path)
        if (data.get("skill_name") != skill_name
                or resource_path(data.get("relative_path")) != path
                or not isinstance(data.get("content"), str)):
            raise ExecutionError("Invalid /load_resource response")
        return data["content"]

    def fetch_files(self, skill_name, entrypoint):
        skill = self.load_skill(skill_name)
        # The registry supplies the instruction body, not the original YAML header.
        files = {"SKILL.md": skill["content"]}
        seen = {"skill.md"}
        paths = []
        for original in skill["resources"]:
            path = resource_path(original)
            if path.casefold() in seen:
                raise ExecutionError(f"Duplicate registry resource path: {original!r}")
            seen.add(path.casefold())
            paths.append((original, path))
        if resource_path(entrypoint) not in {p for _, p in paths}:
            raise ExecutionError(f"Resource is not listed by registry: {entrypoint!r}")
        for original, path in paths:
            try:
                files[path] = self.load_resource(skill_name, original)
            except ExecutionError as exc:
                raise ExecutionError(
                    f"Cannot download {skill_name}/{path}. This API supports UTF-8 text only. {exc}"
                ) from exc
        return files
