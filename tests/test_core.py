import json

from radar import issue_body as ib
from radar import olx, scan
from radar.ads import Ad, normalize_url, olx_listing_id

NOW = "2026-01-01T00:00:00Z"


def _ad(**kw):
    base = {"title": "Repasse apartamento", "url": "https://www.olx.com.br/vi/x-1234567",
            "source": "s", "collected_at": NOW}
    base.update(kw)
    return Ad(**base)


def test_normalize_url_strips_tracking():
    url = normalize_url("https://www.olx.com.br/vi/ap-999?utm_source=x&ref=y")
    assert "utm_source" not in url
    assert "ref=y" in url


def test_listing_id_from_url():
    assert olx_listing_id("https://www.olx.com.br/vi/apartamento-repasse-1234567890") == "1234567890"
    assert olx_listing_id("https://www.olx.com.br/sem-id") is None


def test_dedup_key_prefers_external_id():
    a = _ad(external_id="111", url="https://www.olx.com.br/vi/a-111")
    b = _ad(external_id="111", url="https://www.olx.com.br/vi/a-111?utm_source=z")
    assert a.dedup_key() == b.dedup_key()


def test_dedup_key_differs_by_id():
    assert _ad(external_id="1").dedup_key() != _ad(external_id="2").dedup_key()


def test_keep_filters_include_exclude_and_price():
    s = {"include_keywords": ["repasse"], "exclude_keywords": ["ponto comercial"],
         "min_price": 10000, "max_price": 200000}
    assert scan.keep(_ad(title="Repasse de casa", price=150000), s)
    assert not scan.keep(_ad(title="Casa à venda", price=150000), s)  # sem include
    assert not scan.keep(_ad(title="Repasse ponto comercial", price=150000), s)  # exclude
    assert not scan.keep(_ad(title="Repasse", price=5000), s)  # abaixo do mínimo
    assert not scan.keep(_ad(title="Repasse", price=999999), s)  # acima do máximo


def test_keep_without_price_passes_range():
    s = {"include_keywords": ["repasse"], "exclude_keywords": [], "min_price": 10000, "max_price": None}
    assert scan.keep(_ad(title="Repasse", price=None), s)


def test_issue_body_roundtrip():
    ad = _ad(external_id="42", price=120000, location="São José dos Campos, Centro", by_owner=True)
    body = ib.render_new_body(ad, first_seen=NOW)
    parsed = ib.parse(body)
    assert parsed is not None
    assert parsed.key == ad.dedup_key()
    assert parsed.price == 120000
    assert parsed.first_seen == NOW


def test_sanitize_neutralizes_mentions_and_refs():
    out = ib.sanitize("veja @alguem no #123 http://evil.test")
    assert "@\u200b".encode() or "@&#8203;" in out
    assert "&#8203;" in out
    assert ":&#8203;//" in out


def test_extract_ads_reads_next_data():
    data = {"props": {"listings": [
        {"listId": 777, "subject": "Repasse apê", "url": "https://www.olx.com.br/vi/ap-777",
         "price": "R$ 120.000",
         "location": {"municipality": "São José dos Campos", "neighbourhood": "Centro"}},
    ]}}
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'
    ads = olx.extract_ads(html, source="t", now=NOW)
    assert len(ads) == 1
    assert ads[0].external_id == "777"
    assert ads[0].price == 120000
    assert ads[0].location == "São José dos Campos, Centro"


def test_extract_ads_empty_without_next_data():
    assert olx.extract_ads("<html>no data</html>", source="t", now=NOW) == []
