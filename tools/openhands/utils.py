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
        raise RuntimeError("缺少 SKILL_REGISTRY_URL")
    with requests.get(
        urljoin(base_url, endpoint),
        params=params,
        timeout=None,
    ) as response:
        if not response.ok:
            raise RuntimeError(f"注册中心请求失败，HTTP {response.status_code}")
        return json_text(response.json())

def post_json(endpoint: str, params: dict, *, registry=None) -> str:
    """提交 JSON 请求体，将 JSON 响应序列化为工具返回文本。"""
    if registry is not None:
        return json_text(registry.request(endpoint, params))
    base_url = os.environ.get("SKILL_REGISTRY_URL")
    if not base_url:
        raise RuntimeError("缺少 SKILL_REGISTRY_URL")
    with requests.post(
        urljoin(base_url, endpoint),
        json=params,
        timeout=None,
    ) as response:
        if not response.ok:
            raise RuntimeError(f"注册中心请求失败，HTTP {response.status_code}")
        return json_text(response.json())
