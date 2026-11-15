"""Modelo normalizado de anúncio e limpeza de URL.

Adaptado do JobPosting do job-hunter. A URL canônica (sem rastreamento) é a
chave de deduplicação: anúncios da OLX têm ID estável no final da URL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIX = ("utm_", "xtmc", "xtnp")
_OLX_ID = re.compile(r"-(\d{6,})(?:$|[/?#])")


def normalize_url(url: str) -> str:
    """URL HTTP(S) sem credenciais, fragmento nem parâmetros de rastreamento."""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("URL precisa ser HTTP(S) com host")
    if parts.username or parts.password:
        raise ValueError("URL com credenciais embutidas é rejeitada")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIX)
    ]
    host = parts.hostname.lower()
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, urlencode(query), ""))


def olx_listing_id(url: str) -> str | None:
    """ID numérico do anúncio no final da URL da OLX, se presente."""
    m = _OLX_ID.search(url or "")
    return m.group(1) if m else None


@dataclass
class Ad:
    title: str
    url: str  # canônica, já normalizada
    source: str  # nome da busca que achou (ex.: imoveis-repasse-sp)
    collected_at: str
    external_id: str | None = None  # ID da OLX, quando extraível da URL
    price: int | None = None  # em reais
    location: str | None = None  # "Cidade, Bairro"
    posted_label: str | None = None  # data relativa crua da OLX ("Hoje, 19:27")
    by_owner: bool = False  # selo "Direto com o proprietário"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("title", "url", "source", "collected_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"campo obrigatório vazio: {name}")

    def dedup_key(self) -> str:
        """Chave estável de deduplicação (invariante: um anúncio = uma Issue)."""
        if self.external_id:
            basis = f"olx:{self.external_id}"
        else:
            basis = f"url:{self.url}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
