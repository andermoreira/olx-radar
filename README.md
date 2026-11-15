# OLX Radar

Monitora anúncios de **repasse** na OLX (imóvel, carro, carta de consórcio) por categoria, região e faixa de preço, e abre uma GitHub Issue por anúncio novo para você triar e contatar à mão.

## Como funciona (arquitetura "C": coleta local + publicação no Actions)

A OLX bloqueia scraping de IPs de datacenter (os runners do GitHub Actions tomam 403/CAPTCHA). Por isso a busca roda na **sua máquina** (IP residencial) e só a publicação roda no GitHub.

```
sua máquina (cron/launchd)          GitHub
┌─────────────────────────┐        ┌──────────────────────────┐
│ radar scan              │        │ Action: radar publish    │
│  - busca a OLX          │ push   │  - dedup por URL          │
│  - normaliza anúncios   ├──────► │  - cria/atualiza Issues   │
│  - grava results.json   │        │  - avisa queda de preço   │
└─────────────────────────┘        └──────────────────────────┘
   toca a OLX, não o GitHub          toca o GitHub, não a OLX
```

Fronteira de privilégio: o scraper nunca tem o token do GitHub; o publicador nunca acessa a OLX.

## Contato é 100% manual

O projeto só te avisa de anúncios novos. Você abre o anúncio, decide e fala com o vendedor. Nada é automatizado.

## Legalidade e limites

Scraping da OLX contraria os termos de uso do site; o risco é de quem usa. O projeto usa throttling educado (uma requisição por busca por execução, pausa entre buscas) e **não** tenta burlar CAPTCHA ou bloqueio de acesso. Se a OLX responder 403/CAPTCHA, a busca daquela execução falha e nada é publicado.

## Estado do projeto

Esqueleto inicial. Veja `docs/fluxo-job-hunter.md` para a referência de design e `docs/plano.md` para o roteiro.

## Uso (local)

```bash
make venv          # cria .venv e instala dependências
make scan          # busca a OLX, grava results.json
```

As buscas (categoria, região, termos, faixa de preço) ficam em `config/searches.yaml`.
