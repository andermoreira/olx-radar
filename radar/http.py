"""Busca HTTP educada, só para rodar na máquina local.

Não tenta burlar CAPTCHA nem bloqueio: 403/429 viram Blocked, a busca daquela
execução falha e nada é publicado. User-Agent honesto, um request por busca.
"""

from __future__ import annotations

import gzip
import urllib.error
import urllib.request

UA = "olx-radar/0.1 (monitor pessoal de anuncios; contato manual)"
MAX_BYTES = 8 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # blocked | network | too_large | http


def fetch_text(url: str, *, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError("too_large", url)
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429, 503):
            raise FetchError("blocked", f"HTTP {exc.code}") from None
        raise FetchError("http", f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise FetchError("network", str(exc.reason)) from None
    except OSError as exc:
        raise FetchError("network", str(exc)) from None
