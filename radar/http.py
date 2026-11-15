"""Busca HTTP educada, só para rodar na máquina local.

Utiliza curl_cffi para emular a pilha TLS/HTTP2 de um navegador moderno, evitando
bloqueio falso-positivo por detecção de TLS fingerprint do OpenSSL/urllib.
Não tenta burlar CAPTCHA nem bloqueio: 403/429 viram Blocked, a busca daquela
execução falha e nada é publicado.
"""

from __future__ import annotations

from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError

MAX_BYTES = 8 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # blocked | network | too_large | http


def fetch_text(url: str, *, timeout: int = 20) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        resp = requests.get(
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=timeout,
        )
        if resp.status_code in (403, 429, 503):
            raise FetchError("blocked", f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FetchError("http", f"HTTP {resp.status_code}")
        if len(resp.content) > MAX_BYTES:
            raise FetchError("too_large", url)
        return resp.text
    except RequestsError as exc:
        raise FetchError("network", str(exc)) from None
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError("network", str(exc)) from None
