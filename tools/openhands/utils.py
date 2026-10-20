import os
import json
import requests
from urllib.parse import urljoin

def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def get_json(endpoint: str, params: dict[str, str], *, registry=None) -> str:
    """对应原代码的 request()：读取 JSON，再序列化为文本。"""
    if registry is not None:
        return json_text(registry.request(endpoint, params))
    base_url = os.environ.get("SKILL_REGISTRY_URL")
    if not base_url:
        raise RuntimeError("Missing SKILL_REGISTRY_URL")
    with requests.get(
        urljoin(base_url, endpoint),
        params=params,
        timeout=None,
    ) as response:
        if not response.ok:
            raise RuntimeError(f"Registry HTTP {response.status_code}")
        return json_text(response.json())

def post_json(endpoint: str, params: dict[str, str]) -> str:
    """对应原代码的 request()：读取 JSON，再序列化为文本。"""
    base_url = os.environ.get("SKILL_REGISTRY_URL")
    if not base_url:
        raise RuntimeError("Missing SKILL_REGISTRY_URL")
    with requests.post(
        urljoin(base_url, endpoint),
        data=params,
        timeout=None,
    ) as response:
        if not response.ok:
            raise RuntimeError(f"Registry HTTP {response.status_code}")
        return json_text(response.json())
