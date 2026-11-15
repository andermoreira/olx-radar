"""Parser da busca pública da OLX.

A OLX renderiza os resultados num JSON embutido (`__NEXT_DATA__`). Ler esse
JSON é mais estável que parsear o HTML. A estrutura interna pode mudar; por
isso a extração é defensiva e o scan avisa quando não acha nada.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .ads import Ad, normalize_url, olx_listing_id

BASE = "https://www.olx.com.br"
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
_PRICE_DIGITS = re.compile(r"\d+")


def build_url(path: str, term: str | None) -> str:
    url = f"{BASE}/{path.strip('/')}"
    if term:
        from urllib.parse import quote_plus

        url += f"?q={quote_plus(term)}"
    return url


def _parse_price(raw: Any) -> int | None:
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        digits = "".join(_PRICE_DIGITS.findall(raw))
        return int(digits) if digits else None
    return None


def _location(listing: dict[str, Any]) -> str | None:
    loc = listing.get("location") or listing.get("locationDetails") or {}
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    if isinstance(loc, dict):
        parts = [loc.get("municipality") or loc.get("city"), loc.get("neighbourhood")]
        parts = [p for p in parts if isinstance(p, str) and p.strip()]
        return ", ".join(parts) or None
    return None


def _find_balanced_array(text: str, start_index: int) -> str | None:
    bracket_pos = text.find("[", start_index)
    if bracket_pos == -1:
        return None
    depth = 0
    in_escape = False
    in_quote = False
    for i in range(bracket_pos, len(text)):
        char = text[i]
        if in_escape:
            in_escape = False
            continue
        if char == "\\":
            in_escape = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if not in_quote:
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return text[bracket_pos : i + 1]
    return None


def _extract_next_f_listings(html: str) -> list[dict[str, Any]]:
    """Extrai lista de anúncios do payload do Next.js App Router (self.__next_f)."""
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        chunk = m.group(1)
        for marker in (r'\"ads\":', '"ads":'):
            pos = chunk.find(marker)
            if pos != -1:
                sub = chunk[pos + len(marker) :]
                # Desescapa aspas para parser de JSON padrão
                if marker.startswith(r'\"'):
                    sub = sub.replace(r'\"', '"').replace(r"\\", "\\")
                arr_str = _find_balanced_array(sub, 0)
                if arr_str:
                    try:
                        data = json.loads(arr_str)
                        if isinstance(data, list) and data:
                            return [item for item in data if isinstance(item, dict)]
                    except ValueError:
                        continue
    return []


def _iter_listings(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Caça a lista de anúncios no JSON do Next (legado __NEXT_DATA__), tolerante a mudança de forma."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # Um anúncio de verdade tem url/listId e algum título.
            if ("url" in node or "listId" in node) and (node.get("subject") or node.get("title")):
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data.get("props", data))
    return out


def extract_ads(html: str, *, source: str, now: str) -> list[Ad]:
    raw_listings: list[dict[str, Any]] = _extract_next_f_listings(html)

    if not raw_listings:
        m = _NEXT_DATA.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                raw_listings = _iter_listings(data)
            except ValueError:
                raw_listings = []

    if not raw_listings:
        return []

    ads: list[Ad] = []
    seen: set[str] = set()
    for listing in raw_listings:
        raw_url = listing.get("url")
        if not isinstance(raw_url, str):
            continue
        try:
            url = normalize_url(raw_url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        title = listing.get("subject") or listing.get("title") or ""
        if not isinstance(title, str) or not title.strip():
            continue
        ad = Ad(
            title=title.strip(),
            url=url,
            source=source,
            collected_at=now,
            external_id=str(listing.get("listId") or "") or olx_listing_id(url),
            price=_parse_price(listing.get("price") or listing.get("priceValue")),
            location=_location(listing),
            posted_label=listing.get("date") if isinstance(listing.get("date"), str) else None,
            by_owner=bool(listing.get("professionalAd") is False) or _owner_tag(listing),
        )
        ads.append(ad)
    return ads


def _owner_tag(listing: dict[str, Any]) -> bool:
    for key in ("sellerType", "accountType"):
        v = listing.get(key)
        if isinstance(v, str) and "owner" in v.lower():
            return True
    return False
