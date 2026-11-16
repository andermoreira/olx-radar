"""CLI do OLX Radar.

- `radar scan`    : roda local; busca a OLX e grava results.json.
- `radar publish` : roda no GitHub Actions; cria/atualiza Issues.
- `radar init`    : cria as labels gerenciadas.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import publish as pub
from . import scan
from .ads import Ad
from .github import GitHubClient

ROOT = Path(__file__).resolve().parent.parent
SEARCHES_FILE = ROOT / "config" / "searches.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def cmd_scan(args) -> int:
    searches = scan.load_searches(Path(args.searches))["searches"]
    if not searches:
        print("nenhuma busca em searches.yaml")
        return 1
    results = scan.collect(searches, now=_now())
    scan.write_results(results, Path(args.out))
    any_ok = False
    for r in results:
        if r["status"] == "failed":
            print(f"{r['name']}: FALHOU ({r['error']})")
        elif r["status"] == "empty":
            print(f"{r['name']}: 0 anúncios (busca vazia ou HTML da OLX mudou)")
        else:
            any_ok = True
            print(f"{r['name']}: {len(r['ads'])} anúncio(s) após filtro (de {r.get('found', 0)} encontrados)")
    blocked = [r for r in results if r["status"] == "failed" and r["error"] == "blocked"]
    if blocked:
        print("\nAviso: a OLX bloqueou (403/429). Rode da sua máquina (IP residencial), não de datacenter.")
    # Falha total só se tudo falhou; busca vazia não é erro.
    return 0 if any_ok or any(r["status"] == "empty" for r in results) else 1


def _client() -> GitHubClient | None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("ERRO GITHUB_TOKEN e GITHUB_REPOSITORY são obrigatórios")
        return None
    return GitHubClient(token, repo)


def cmd_publish(args) -> int:
    client = _client()
    if client is None:
        return 1
    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    ads = [Ad(**a) for a in data["ads"]]
    pub.ensure_labels(client)
    report = pub.publish(ads, client, now=_now())
    print(
        f"criadas: {report.created}, atualizadas: {report.updated}, "
        f"sem mudança: {report.unchanged}, quedas de preço: {report.price_drops}, erros: {report.errors}"
    )
    if report.fatal:
        print(f"ERRO {report.fatal}")
        return 1
    return 0


def cmd_init(args) -> int:
    client = _client()
    if client is None:
        return 1
    created = pub.ensure_labels(client)
    print(f"labels criadas: {', '.join(created) if created else 'nenhuma (todas já existiam)'}")
    return 0


def cmd_triage(args) -> int:
    client = _client()
    if client is None:
        return 1
    from . import triage

    success = triage.process_triage(
        client,
        args.issue,
        comment_body=args.comment,
        is_closed_event=args.closed,
    )
    if success:
        print(f"Issue #{args.issue}: triagem aplicada")
    else:
        print(f"Issue #{args.issue}: nenhuma ação identificada")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radar")
    sub = p.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scan", help="busca a OLX e grava results.json (rode local)")
    sc.add_argument("--searches", default=str(SEARCHES_FILE))
    sc.add_argument("--out", default="results.json")
    sc.set_defaults(func=cmd_scan)

    pb = sub.add_parser("publish", help="cria/atualiza Issues a partir do results.json")
    pb.add_argument("--results", default="results.json")
    pb.set_defaults(func=cmd_publish)

    it = sub.add_parser("init", help="cria as labels gerenciadas")
    it.set_defaults(func=cmd_init)

    tr = sub.add_parser("triage", help="aplica triagem em uma Issue por comentário ou fechamento")
    tr.add_argument("--issue", type=int, required=True, help="número da Issue")
    tr.add_argument("--comment", default=None, help="corpo do comentário")
    tr.add_argument("--closed", action="store_true", help="se o evento foi de issue fechada")
    tr.set_defaults(func=cmd_triage)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
