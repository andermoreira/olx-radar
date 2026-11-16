import json

from radar import http, olx, scan, triage
from radar import issue_body as ib
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


def test_extract_ads_reads_next_f_rsc_payload():
    ad_payload = [{
        "listId": 888,
        "subject": "Repasse casa",
        "url": "https://sp.olx.com.br/vale-do-paraiba/imoveis/repasse-casa-888",
        "priceValue": "R$ 150.000",
        "location": "São José dos Campos, Jardim das Indústrias",
        "professionalAd": False,
    }]
    encoded_payload = json.dumps(ad_payload).replace('"', '\\"')
    js_chunk = f'\\"ads\\":{encoded_payload}'
    html = f'<html><script>self.__next_f.push([1,"1:foo\\n2:{js_chunk}"]);</script></html>'
    ads = olx.extract_ads(html, source="t", now=NOW)
    assert len(ads) == 1
    assert ads[0].external_id == "888"
    assert ads[0].title == "Repasse casa"
    assert ads[0].price == 150000
    assert ads[0].location == "São José dos Campos, Jardim das Indústrias"
    assert ads[0].by_owner is True


def test_fetch_text_blocked_status(monkeypatch):
    class FakeResponse:
        status_code = 403
        content = b"blocked"
        text = "blocked"

    from curl_cffi import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse())

    import pytest
    with pytest.raises(http.FetchError) as exc_info:
        http.fetch_text("https://example.com")
    assert exc_info.value.kind == "blocked"


def test_fetch_text_success(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b"<html>ok</html>"
        text = "<html>ok</html>"

    from curl_cffi import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse())

    result = http.fetch_text("https://example.com")
    assert result == "<html>ok</html>"


def test_triage_evaluate_comment():
    assert triage.evaluate_comment("❤️") == "interested"
    assert triage.evaluate_comment("/interessante") == "interested"
    assert triage.evaluate_comment("interessante!") == "interested"
    assert triage.evaluate_comment("📞") == "contacted"
    assert triage.evaluate_comment("/contato") == "contacted"
    assert triage.evaluate_comment("❌") == "discarded"
    assert triage.evaluate_comment("/descartar") == "discarded"
    assert triage.evaluate_comment("bom dia") is None


def test_triage_apply_action():
    initial = ["radar:new"]
    labels, close = triage.apply_triage("interested", initial)
    assert "radar:interested" in labels
    assert "radar:new" not in labels
    assert close is False

    labels, close = triage.apply_triage("discarded", labels)
    assert "radar:discarded" in labels
    assert "radar:interested" not in labels
    assert close is True


def test_triage_process_comment():
    class FakeClient:
        def __init__(self):
            self.labels = ["radar:new"]
            self.closed = False

        def get_issue(self, num):
            return {
                "number": num,
                "labels": [{"name": lb} for lb in self.labels],
                "state": "closed" if self.closed else "open",
            }

        def set_labels(self, num, labels):
            self.labels = labels

        def close_issue(self, num):
            self.closed = True

    client = FakeClient()
    ok = triage.process_triage(client, 1, comment_body="❤️")
    assert ok is True
    assert "radar:interested" in client.labels
    assert "radar:new" not in client.labels
    assert client.closed is False

    ok_disc = triage.process_triage(client, 1, comment_body="❌")
    assert ok_disc is True
    assert "radar:discarded" in client.labels
    assert client.closed is True




