"""Cliente mínimo da REST API do GitHub para Issues e labels.

Adaptado do job-hunter: respeita rate limit secundário e nunca repete no erro
o corpo enviado (pode conter dados do anúncio).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"
MAX_RETRIES = 3


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.message = message


class RateLimited(GitHubError):
    pass


class GitHubClient:
    def __init__(self, token: str, repo: str, *, sleep=time.sleep):
        self.repo = repo
        self._sleep = sleep
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "olx-radar",
        }

    def _request(self, method: str, url: str, payload: Any = None) -> tuple[dict[str, str], Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = dict(self._headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(MAX_RETRIES + 1):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return {k.lower(): v for k, v in resp.headers.items()}, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    data = json.loads(raw) if raw else None
                except ValueError:
                    data = None
                h = {k.lower(): v for k, v in exc.headers.items()}
                if exc.code in (403, 429) and (
                    "retry-after" in h or h.get("x-ratelimit-remaining") == "0"
                ):
                    if attempt == MAX_RETRIES:
                        raise RateLimited(exc.code, "rate limit") from None
                    wait = int(h.get("retry-after", 60 * 2**attempt))
                    self._sleep(wait)
                    continue
                msg = data.get("message", "") if isinstance(data, dict) else ""
                raise GitHubError(exc.code, msg[:120]) from None
            except (OSError, ValueError) as exc:
                raise GitHubError(0, f"network: {exc}") from None
        raise AssertionError("inalcançável")

    def _url(self, path: str) -> str:
        return f"{API}/repos/{self.repo}{path}"

    def list_issues(self) -> list[dict[str, Any]]:
        url = self._url("/issues?state=all&per_page=100&page=1")
        issues: list[dict[str, Any]] = []
        while url:
            headers, data = self._request("GET", url)
            issues += [i for i in data if "pull_request" not in i]
            url = _next_link(headers.get("link", ""))
        return issues

    def get_issue(self, number: int) -> dict[str, Any]:
        _, data = self._request("GET", self._url(f"/issues/{int(number)}"))
        return data

    def close_issue(self, number: int) -> None:
        self._request("PATCH", self._url(f"/issues/{int(number)}"), {"state": "closed"})

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        payload = {"title": title, "body": body, "labels": labels}
        _, data = self._request("POST", self._url("/issues"), payload)
        return int(data["number"])

    def update_issue_body(self, number: int, body: str) -> None:
        self._request("PATCH", self._url(f"/issues/{int(number)}"), {"body": body})

    def create_comment(self, number: int, body: str) -> None:
        self._request("POST", self._url(f"/issues/{int(number)}/comments"), {"body": body})

    def set_labels(self, number: int, labels: list[str]) -> None:
        self._request("PUT", self._url(f"/issues/{int(number)}/labels"), {"labels": labels})

    def list_labels(self) -> list[str]:
        url = self._url("/labels?per_page=100&page=1")
        names: list[str] = []
        while url:
            headers, data = self._request("GET", url)
            names += [label["name"] for label in data]
            url = _next_link(headers.get("link", ""))
        return names

    def create_label(self, name: str, color: str, description: str) -> None:
        payload = {"name": name, "color": color, "description": description}
        self._request("POST", self._url("/labels"), payload)


def _next_link(link: str) -> str | None:
    for part in link.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url if url.startswith(API + "/") else None
    return None
