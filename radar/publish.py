"""Publicação: uma Issue por anúncio único, idempotente.

Dedup pela chave do anúncio (ID da OLX ou URL), gravada em comentário HTML.
Rodar de novo não duplica: anúncio conhecido atualiza; preço que muda vira
um comentário de aviso. Só o bloco gerado é reescrito; texto seu é preservado.
"""

from __future__ import annotations

import time

from . import issue_body as ib
from .ads import Ad
from .github import GitHubClient, GitHubError, RateLimited

WRITE_PAUSE_SECONDS = 1

MANAGED_LABELS = {
    "radar:new": ("0e8a16", "Anúncio novo, aguardando triagem"),
    "radar:interested": ("1d76db", "Marcado como interessante"),
    "radar:contacted": ("5319e7", "Você já entrou em contato"),
    "radar:discarded": ("e4e669", "Descartado"),
    "radar:price-drop": ("d93f0b", "Preço caiu desde que foi visto"),
}
PERSON_STATES = {"radar:interested", "radar:contacted", "radar:discarded"}


class PublishReport:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.unchanged = 0
        self.price_drops = 0
        self.errors = 0
        self.fatal: str | None = None


def ensure_labels(client: GitHubClient) -> list[str]:
    existing = set(client.list_labels())
    created = []
    for name, (color, desc) in MANAGED_LABELS.items():
        if name not in existing:
            client.create_label(name, color, desc)
            created.append(name)
    return created


def _labels(issue) -> list[str]:
    return [lb["name"] if isinstance(lb, dict) else lb for lb in issue.get("labels", [])]


def publish(
    ads: list[Ad], client: GitHubClient, *, now: str, max_writes: int = 100, sleep=time.sleep
) -> PublishReport:
    report = PublishReport()
    index: dict[str, dict] = {}
    for issue in client.list_issues():
        parsed = ib.parse(issue.get("body"))
        if parsed and parsed.key:
            index[parsed.key] = {
                "number": issue["number"],
                "parsed": parsed,
                "labels": _labels(issue),
                "body": issue.get("body") or "",
            }

    writes = 0
    for ad in ads:
        key = ad.dedup_key()
        existing = index.get(key)
        if writes >= max_writes:
            break
        try:
            if existing is None:
                if writes:
                    sleep(WRITE_PAUSE_SECONDS)
                writes += 1
                body = ib.render_new_body(ad, first_seen=now)
                client.create_issue(ib.render_title(ad), body, ["radar:new"])
                report.created += 1
            else:
                number = existing["number"]
                parsed = existing["parsed"]
                old_price = parsed.price
                # Regrava só o bloco gerado, preservando o seu texto fora dele.
                new_block = ib.render_block(ad, first_seen=parsed.first_seen or now)
                new_body = ib.merge(existing["body"], new_block)
                price_dropped = old_price is not None and ad.price is not None and ad.price < old_price
                if new_body is None or (new_body == existing["body"] and not price_dropped):
                    report.unchanged += 1
                    continue
                if writes:
                    sleep(WRITE_PAUSE_SECONDS)
                writes += 1
                client.update_issue_body(number, new_body)
                if price_dropped:
                    labels = existing["labels"]
                    if "radar:price-drop" not in labels:
                        client.set_labels(number, [*labels, "radar:price-drop"])
                    client.create_comment(
                        number,
                        f"💸 Preço caiu: de {_brl(old_price)} para {_brl(ad.price)}.",
                    )
                    report.price_drops += 1
                report.updated += 1
        except RateLimited:
            report.fatal = "rate_limited"
            break
        except GitHubError:
            report.errors += 1
            continue
    return report


def _brl(value: int) -> str:
    return f"R$ {value:,.0f}".replace(",", ".")
