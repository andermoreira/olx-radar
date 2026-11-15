"""Corpo das Issues de anúncio.

Como no job-hunter: só o bloco entre START e END é do bot; texto fora dele é
seu e nunca é reescrito. Metadados (chave de dedup, preço, visto em) ficam em
comentários HTML dentro do bloco. Conteúdo da OLX passa por `sanitize`.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .ads import Ad

START = "<!-- olx-radar:start -->"
END = "<!-- olx-radar:end -->"
_KEY_LINE = re.compile(r"^<!-- olx-radar:key=([0-9a-f]{32}) -->$")
_PRICE_LINE = re.compile(r"^<!-- olx-radar:price=(\d+) -->$")
_FIRST_SEEN_LINE = re.compile(r"^<!-- olx-radar:first-seen=([0-9T:Z-]{10,25}) -->$")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()!|~])")
TITLE_MAX = 256


def sanitize(text: str | None, *, max_chars: int = 300) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) > max_chars:
        flat = flat[:max_chars].rstrip() + "…"
    flat = re.sub(r"olx-radar:", "olx-radar ", flat, flags=re.IGNORECASE)
    flat = flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    flat = _MD_SPECIAL.sub(r"\\\1", flat)
    zw = "&#8203;"
    flat = flat.replace("#", "#" + zw).replace("@", "@" + zw).replace("://", ":" + zw + "//")
    return flat


def _url(url: str) -> str:
    return "<" + quote(url, safe=":/?=&%-._~+,;@!$*'#") + ">"


def render_title(ad: Ad) -> str:
    title = re.sub(r"[\x00-\x1f\x7f]+", " ", ad.title)
    title = re.sub(r"\s+", " ", title).strip().replace("#", "#\u200b").replace("@", "@\u200b")
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1] + "…"


def _price(value: int | None) -> str:
    return f"R$ {value:,.0f}".replace(",", ".") if value is not None else "não informado"


def render_block(ad: Ad, *, first_seen: str) -> str:
    s = sanitize
    lines = [
        START,
        f"<!-- olx-radar:key={ad.dedup_key()} -->",
        f"<!-- olx-radar:first-seen={first_seen} -->",
    ]
    if ad.price is not None:
        lines.append(f"<!-- olx-radar:price={ad.price} -->")
    lines += [
        "### Anúncio",
        "",
        f"- **Preço:** {_price(ad.price)}",
        f"- **Local:** {s(ad.location) if ad.location else 'não informado'}",
        f"- **Direto com proprietário:** {'sim' if ad.by_owner else 'não/indefinido'}",
        f"- **Publicado:** {s(ad.posted_label) if ad.posted_label else 'não informado'}",
        f"- **Busca:** {s(ad.source)}",
        f"- **Visto pela primeira vez:** {first_seen}",
        f"- **Link:** {_url(ad.url)}",
        END,
    ]
    return "\n".join(lines)


def render_new_body(ad: Ad, *, first_seen: str) -> str:
    return render_block(ad, first_seen=first_seen) + "\n"


class Parsed:
    def __init__(self, key: str | None, price: int | None, first_seen: str | None, start: int, end: int):
        self.key = key
        self.price = price
        self.first_seen = first_seen
        self.start = start
        self.end = end


def parse(body: str | None) -> Parsed | None:
    lines = (body or "").split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.strip() == START]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        return None
    inner = [ln.strip() for ln in lines[starts[0] + 1 : ends[0]]]
    key = next((m.group(1) for ln in inner if (m := _KEY_LINE.match(ln))), None)
    price = next((int(m.group(1)) for ln in inner if (m := _PRICE_LINE.match(ln))), None)
    first = next((m.group(1) for ln in inner if (m := _FIRST_SEEN_LINE.match(ln))), None)
    return Parsed(key, price, first, starts[0], ends[0])


def merge(body: str, new_block: str) -> str | None:
    parsed = parse(body)
    if parsed is None:
        return None
    lines = body.split("\n")
    return "\n".join([*lines[: parsed.start], new_block, *lines[parsed.end + 1 :]])
