from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"


class QuotaError(Exception):
    pass


@dataclass(frozen=True)
class QuotaEntry:
    remaining_percent: float
    reset_at: datetime | None


@dataclass(frozen=True)
class QuotaSnapshot:
    limit_5h: QuotaEntry
    limit_7d: QuotaEntry | None
    fetched_at: datetime


def _credential_path() -> Path:
    home = Path(os.environ.get("KIMI_CODE_HOME", "~/.kimi-code")).expanduser()
    return home / "credentials" / "kimi-code.json"


def _access_token() -> str:
    path = _credential_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QuotaError("未找到 Kimi Code 登录凭据，请先在 Kimi Code 中运行 /login") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise QuotaError("无法读取 Kimi Code 登录凭据，请重新运行 /login") from exc

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise QuotaError("Kimi Code 登录凭据无效，请重新运行 /login")
    return token


def _reset_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _quota_entry(raw: Any) -> QuotaEntry | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("used_ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    used_ratio = float(value)
    if 1 < used_ratio <= 100:
        used_ratio /= 100
    if not 0 <= used_ratio <= 1:
        return None
    return QuotaEntry(
        remaining_percent=round((1 - used_ratio) * 100, 1),
        reset_at=_reset_at(raw.get("reset_time")),
    )


def parse_usage_payload(payload: Any) -> QuotaSnapshot:
    if not isinstance(payload, dict) or not isinstance(payload.get("usages"), dict):
        raise QuotaError("额度接口返回了无法识别的数据")
    usages = payload["usages"]
    limit_5h = _quota_entry(usages.get("limit_5h"))
    if limit_5h is None:
        raise QuotaError("额度接口未返回有效的 5 小时额度")
    return QuotaSnapshot(
        limit_5h=limit_5h,
        limit_7d=_quota_entry(usages.get("limit_7d")),
        fetched_at=datetime.now(timezone.utc),
    )


def fetch_quota(timeout: float = 10) -> QuotaSnapshot:
    token = _access_token()
    base_url = os.environ.get("KIMI_CODE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/usages",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "kimi-whale-widget/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise QuotaError("登录状态已失效，请在 Kimi Code 中重新 /login") from exc
        if exc.code == 429:
            raise QuotaError("额度查询过于频繁，请稍后刷新") from exc
        raise QuotaError(f"额度查询失败：HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise QuotaError("网络连接失败，请检查网络后重试") from exc
    except json.JSONDecodeError as exc:
        raise QuotaError("额度接口返回了无效响应") from exc
    return parse_usage_payload(payload)
