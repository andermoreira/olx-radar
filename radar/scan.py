"""Coleta: roda as buscas, aplica filtros, grava results.json.

Roda na máquina local (IP residencial). Uma requisição por busca, com pausa
entre elas. Uma busca bloqueada não derruba as outras.
"""

from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from . import olx
from .ads import Ad
from .http import FetchError, fetch_text

PAUSE_SECONDS = 4


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def load_searches(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    searches = data.get("searches") or []
    for s in searches:
        s.setdefault("include_keywords", defaults.get("include_keywords", []))
        s.setdefault("exclude_keywords", defaults.get("exclude_keywords", []))
    return {"searches": searches}


def keep(ad: Ad, search: dict[str, Any]) -> bool:
    title = _fold(ad.title)
    include = [_fold(k) for k in search.get("include_keywords") or []]
    exclude = [_fold(k) for k in search.get("exclude_keywords") or []]
    if include and not any(k in title for k in include):
        return False
    if any(k in title for k in exclude):
        return False
    if ad.price is not None:
        lo, hi = search.get("min_price"), search.get("max_price")
        if lo is not None and ad.price < lo:
            return False
        if hi is not None and ad.price > hi:
            return False
    return True


def run_search(search: dict[str, Any], *, now: str) -> dict[str, Any]:
    name = search["name"]
    url = olx.build_url(search["path"], search.get("term"))
    try:
        html = fetch_text(url)
    except FetchError as exc:
        return {"name": name, "status": "failed", "error": exc.kind, "ads": []}
    ads = olx.extract_ads(html, source=name, now=now)
    if not ads:
        # Sem anúncios pode ser busca vazia ou mudança no HTML da OLX.
        return {"name": name, "status": "empty", "error": None, "ads": []}
    kept = [ad for ad in ads if keep(ad, search)]
    return {"name": name, "status": "ok", "error": None, "ads": kept, "found": len(ads)}


def collect(searches: list[dict[str, Any]], *, now: str, sleep=time.sleep) -> list[dict[str, Any]]:
    results = []
    for i, search in enumerate(searches):
        if i:
            sleep(PAUSE_SECONDS)
        results.append(run_search(search, now=now))
    return results


def write_results(results: list[dict[str, Any]], path: Path) -> None:
    payload = {
        "searches": [{k: v for k, v in r.items() if k != "ads"} for r in results],
        "ads": [asdict(ad) for r in results for ad in r["ads"]],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
