"""GitHub'daki en son sürümü sorar. Hiçbir kullanıcı bilgisi gönderilmez."""
from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

GITHUB_REPO = "yklcsf/ClipLean"


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def is_newer(candidate: str, current: str = __version__) -> bool:
    try:
        return _parse(candidate) > _parse(current)
    except ValueError:
        return False


def check(timeout: float = 5.0) -> tuple[str, str] | None:
    """Yeni sürüm varsa (sürüm, indirme sayfası), yoksa ya da bağlantı yoksa None."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ClipLean"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        tag, url = data["tag_name"], data["html_url"]
    except (OSError, ValueError, KeyError):
        return None
    return (tag.lstrip("v"), url) if is_newer(tag) else None
