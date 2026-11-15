# Como funciona o fluxo: agendar → pesquisar → classificar → abrir issue

Análise do projeto `job-hunter` (e, de passagem, do `cheatsheets-hub`) para servir de referência a um projeto novo focado em busca de anúncios. O objetivo aqui é descrever o **processo**, não copiar o código.

> `cheatsheets-hub` não tem esse fluxo. Ele é um agregador de repositórios de terceiros via submódulos (`.gitmodules`) e só usa o GitHub Actions para o Dependabot. Toda a mecânica de agendar/pesquisar/classificar/abrir issue vem do `job-hunter`.

## Visão geral em uma frase

Um cron do GitHub Actions dispara um pipeline de jobs com permissões separadas: um busca anúncios em fontes públicas, outro (o único com acesso ao perfil e ao LLM) classifica, e um terceiro cria ou atualiza uma Issue por anúncio único, de forma idempotente.

## As quatro etapas

```
schedule (cron) ──► collect ──► score ──► publish
                   (buscar)   (classificar)  (abrir/atualizar issue)
                     │            │              │
                 results.json  scored/        issues +
                               evaluated.json  digest
```

Cada seta é um **job separado** do Actions, passando dados por artifact (`upload-artifact` / `download-artifact`). A separação não é só organização: é fronteira de privilégio.

### 1. Agendar (`on: schedule`)

Arquivo: `.github/workflows/hunter.yml`.

```yaml
on:
  schedule:
    - cron: "17 7 * * *"
      timezone: "America/Bahia"
  workflow_dispatch:
    inputs:
      operation:
        type: choice
        options: [scan, import, prepare, init, llm-check, mail-check, mail-sample]
        default: scan
```

Pontos de design que valem copiar:

- **Horário "quebrado" (minuto 17).** Fora do início da hora, porque o começo de cada hora é o pico de carga do GitHub Actions e o `schedule` é best-effort (pode atrasar).
- **Fuso explícito** (`timezone`), senão o cron roda em UTC.
- **`workflow_dispatch` como recuperação manual.** Se um agendamento atrasar ou falhar, dá para rodar `scan` à mão. O mesmo dispatch serve para operações pontuais (importar um anúncio por URL, inicializar labels, etc.).
- **`permissions: {}` no topo**, e cada job declara só o que precisa. Padrão de menor privilégio.
- **`concurrency` com `cancel-in-progress: false` e fila** para duas execuções não brigarem pelas mesmas Issues.
- No evento `schedule` os inputs vêm vazios, então o workflow trata "sem operação" como `scan`.

### 2. Pesquisar (job `collect`)

Permissão: só `contents: read`. **Não** tem `issues: write` nem secrets pessoais. É o job que toca conteúdo externo não confiável, então é o mais contido.

Código: `hunter/scan.py` + `hunter/sources/*`.

- Fontes ficam versionadas em `config/sources.yaml` (empresas em ATS Greenhouse/Lever, buscas públicas na Gupy). Um adaptador por fonte, todos com a mesma interface (`collect(cfg, fetch, now=...)`).
- Cada adaptador **captura a própria falha**: uma fonte fora do ar vira `status: failed` e as demais continuam. Falha parcial não derruba a execução; só falha total (todas as fontes configuradas falharam) sai com erro.
- Toda vaga vira um modelo normalizado comum, o `JobPosting` (`hunter/jobs.py`): `company`, `title`, `description`, `source`, `canonical_url`, `external_id`, `location`, `work_mode`, prazo, etc.
- Higiene de dados já aqui:
  - `normalize_url()` remove credenciais embutidas, fragmentos e parâmetros de rastreamento (`utm_*`, `gclid`, `fbclid`...). Isso é o que torna a URL uma chave de deduplicação estável.
  - HTML da descrição é convertido para texto (`html_to_text`), descartando `<script>`/`<style>`.
  - `freshness_confidence` classifica a atualidade do anúncio (`verified_fresh`, `likely_fresh`, `newly_discovered`, `unknown`) a partir da data de publicação e de uma janela configurável.

Saída: `results.json` (lista de fontes + lista de postings), publicado como artifact com retenção de 1 dia.

### 3. Classificar (job `score`)

Permissão: `contents: read` + `issues: read`. É o **único** job que recebe o perfil (`secrets.JOB_HUNTER_PROFILE`) e as chaves de LLM. Ele lê Issues (para não reavaliar o que já foi avaliado) mas **não escreve em nada**.

A classificação acontece em dois estágios:

**3a. Score base determinístico** (`hunter/scoring.py`, comando `hunter score`)

- Roda para toda vaga, **sem LLM**. Rápido, barato, explicável.
- Primeiro aplica **filtros eliminatórios** (palavras-chave excluídas no título, modo de trabalho incompatível). Eliminada → score 0.
- Depois um score 0–10 somando componentes com pesos fixos: competências (máx 5), cargo (2,5), modo de trabalho (1,25), local (1,25). Campo desconhecido vale metade e nunca elimina.
- Competências são medidas por um **dicionário fixo de tecnologias** (`TECHNOLOGIES`), normalizando sinônimos (`dev`→`desenvolvedor`) e formas compostas (`back end`→`backend`), com casamento por borda de palavra para `go`/`java` não casarem errado.
- Detalhe de produto importante: o resultado carrega **só números e IDs** (índices de cargo-alvo, IDs de evidência, nomes de filtro), nunca texto do perfil, porque esse JSON segue para a Issue e para o artifact.
- O score também produz uma **fila** para a avaliação profunda, ordenada por score (`select_for_deep_evaluation`).

**3b. Avaliação profunda por LLM** (`hunter/evaluation.py`, comando `hunter evaluate`)

- Opcional e com `continue-on-error: true`: se o LLM falhar, a publicação segue com o score base.
- Defesas contra prompt injection e vazamento, que são o ponto mais interessante para copiar:
  - A vaga entra **delimitada** (`<vaga>...</vaga>`) e o conteúdo externo tem `<`/`>` escapados (`\u003c`), então o texto do anúncio não consegue fechar a tag nem abrir outra.
  - O modelo responde num **schema fechado** (JSON Schema), sem campo de texto livre: só números, categorias de um `enum` e IDs de evidência que também são um `enum` das evidências aprovadas.
  - Um **validador** reconfere tudo depois e descarta a resposta se algum valor de contato do perfil aparecer (`validate_no_contact`), ou se uma afirmação "atendida" não citar evidência.
  - O prompt recebe só evidências aprovadas, nunca contato nem respostas pessoais.
- Isolamento do provedor: Claude Code CLI rodado sem ferramentas, sem MCP e sem plugins; Groq como backup com schema estrito. A quebra de isolamento faz o step falhar (sinal de segurança visível).

Saída: `evaluated.json`, artifact tratado como PII (deriva do perfil), retenção de 1 dia.

### 4. Abrir a issue (job `publish`)

Permissão: `contents: read` + `issues: write`. Recebe o JSON com score e IDs de evidência, **sem acesso a fontes externas nem a secrets pessoais**.

Código: `hunter/publish.py` + `hunter/issue_body.py`.

**Deduplicação idempotente** (o coração do fluxo):

- Cada anúncio gera três **chaves** (`hunter/issue_body.py::posting_keys`): `source` (hash de `source:external_id`), `url` (hash da URL canônica) e `fallback` (hash de empresa+cargo+local+descrição).
- Ao publicar, o código lê todas as Issues, parseia o bloco gerado e monta um índice chave→número. Um anúncio novo casa com uma Issue existente por qualquer chave primária; se só a `fallback` casar, marca como "possível duplicata" sem mesclar.
- Resultado: rodar de novo **não cria duplicata**. A mesma vaga de fontes/execuções diferentes atualiza a mesma Issue.

**Bloco gerado vs. texto humano:**

- O corpo da Issue tem um bloco delimitado por marcadores HTML (`<!-- job-hunter:generated:start -->` … `:end -->`). Só esse bloco pertence ao bot; qualquer texto fora dele (notas da pessoa) **nunca é reescrito** (`merge`).
- Metadados (chaves, data de primeira vez vista, score, descrição completa em base64) ficam em comentários HTML dentro do bloco, invisíveis na renderização mas parseáveis na próxima execução. É o "banco de dados" do projeto: **o estado vive nas próprias Issues**, não há DB externo.

**Sanitização de conteúdo de terceiros** (`sanitize`), porque título/descrição vêm de fora:

- Escapa HTML e caracteres especiais de Markdown.
- Insere um espaço de largura zero (`&#8203;`) depois de `#`, `@` e em `://` para **quebrar** referências cruzadas a Issues, menções a pessoas e autolinks. (Detalhe: o GitHub decodifica entidades antes de criar esses links, por isso o truque do zero-width.)
- Neutraliza tentativas de forjar os próprios marcadores (`job-hunter:` → `job-hunter `).

**Labels gerenciadas** modelam o ciclo de vida: `job:new`, `job:needs-verification`, `job:evaluation-pending`, `job:shortlisted`, `job:preparing`, `job:ready-for-review`, `job:applied`, `job:closed`, além de `source:*` (origem). A pessoa muda de estado pela label; o bot preserva estados humanos ao atualizar.

**Limites de segurança operacional:**

- `max_issue_writes_per_run` com pausa ≥ 1 s entre escritas, para respeitar o rate limit secundário do GitHub. Escritas além do limite ficam "adiadas" (`deferred`) e entram na próxima execução.
- O cliente HTTP (`hunter/github.py`) respeita `Retry-After` / `X-RateLimit-Reset`, com backoff, e nunca repete no erro o corpo enviado (que pode ter dados da vaga).

### Extra: resumo diário (`hunter digest`)

Roda depois do `publish` nas execuções agendadas. Mantém uma Issue única `job:digest` com: vagas novas acima do corte de score, verificação pendente, prazos próximos e saúde das fontes. O estado entre execuções (último/primeiro resumo) fica em marcadores dentro do próprio bloco gerado dessa Issue. Também comenta uma vez por prazo nas vagas em andamento (grava o marcador `deadline-warned` **antes** de comentar, para no máximo perder um aviso em vez de repetir todo dia).

## Princípios que valem replicar no projeto de anúncios

1. **Agendar com cron + dispatch manual.** Minuto quebrado, fuso explícito, dispatch como recuperação.
2. **Um job por etapa, com permissões mínimas e distintas.** O job que lê a internet não escreve Issues nem vê secrets; o que vê o perfil/LLM não escreve nada; o que escreve Issues não lê a internet. Dados passam por artifact.
3. **Modelo normalizado único** para o item buscado, com limpeza de URL e de HTML já na coleta.
4. **Classificação em dois estágios:** filtro + score determinístico barato primeiro, LLM opcional e tolerante a falha depois.
5. **Deduplicação idempotente por chaves estáveis** (ID da fonte, URL canônica, hash de conteúdo). Rodar de novo atualiza, não duplica.
6. **A Issue é o banco de dados.** Estado em comentários HTML dentro de um bloco gerado; texto humano fora do bloco é intocável.
7. **Todo conteúdo externo é dado, nunca instrução.** Delimitar, escapar, validar com schema fechado, sanitizar antes de publicar; neutralizar menções/refs/autolinks.
8. **Falha parcial não derruba tudo;** falha de segurança (quebra de isolamento, PII vazada) falha visível.

## Mapa de arquivos de referência

| Etapa | Arquivo |
|---|---|
| Agendar + orquestrar | `.github/workflows/hunter.yml` |
| Buscar | `hunter/scan.py`, `hunter/sources/*`, `config/sources.yaml` |
| Modelo do item | `hunter/jobs.py` |
| Classificar (determinístico) | `hunter/scoring.py` |
| Classificar (LLM) | `hunter/evaluation.py`, `prompts/evaluate.md` |
| Abrir/atualizar Issue | `hunter/publish.py`, `hunter/issue_body.py` |
| Resumo diário | `hunter/digest.py` |
| Cliente GitHub (rate limit) | `hunter/github.py` |
| CLI que amarra tudo | `hunter/cli.py` |
| Parâmetros | `config/hunter.yaml` |
