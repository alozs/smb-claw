# Configuração

## Variáveis de ambiente

### config.global

| Variável | Descrição |
|---|---|
| `PROVIDER` | Provedor padrão: `anthropic`, `openrouter`, `codex`, `claude-cli` |
| `MODEL` | Modelo padrão |
| `ADMIN_ID` | Telegram ID do administrador |
| `ACCESS_MODE` | Modo de acesso padrão: `open`, `approval`, `closed` |
| `ANTHROPIC_API_KEY` | Chave Anthropic (opcional se usar OAuth) |
| `OPENROUTER_API_KEY` | Chave OpenRouter |
| `OPENAI_API_KEY` | Chave OpenAI (opcional se usar Codex OAuth) |
| `BUGFIXER_ENABLED` | `true` / `false` |
| `BUGFIXER_TIMES_PER_DAY` | Frequência do Bug Fixer |
| `BUGFIXER_TELEGRAM_TOKEN` | Token para notificações do Bug Fixer |
| `ADMIN_PANEL_URL` | URL pública do painel (ex: `https://painel.seudominio.com`). Se vazio, usa IP local. |

### .env do agente

| Variável | Obrigatório | Descrição |
|---|---|---|
| `TELEGRAM_TOKEN` | sim | Token do @BotFather |
| `BOT_NAME` | — | Nome do agente (padrão: nome da pasta) |
| `MAX_HISTORY` | — | Máx. mensagens no histórico (padrão: 20) |
| `TOOLS` | — | Ferramentas ativas (padrão: none) |
| `WORK_DIR` | — | Sandbox de arquivos (padrão: `<bot>/workspace`) |
| `MODEL` | — | Override do modelo |
| `PROVIDER` | — | Override do provedor |
| `ACCESS_MODE` | — | Override do modo de acesso |
| `GROUP_MODE` | — | `always` ou `mention_only` (para grupos) |
| `DESCRIPTION` | — | Descrição curta (exibida no painel) |

### secrets.env do agente

| Variável | Ferramenta | Descrição |
|---|---|---|
| `DB_URL` | `database` | Connection string do banco |
| `GIT_TOKEN` | `git` | Token GitHub/GitLab |
| `GIT_USER` | `git` | Username git |
| `GIT_EMAIL` | `git` | Email git |
| `GITHUB_TOKEN` | `github` | Token GitHub (fallback: `GIT_TOKEN`) |
| `API_KEY_1` | livre | Chave de API extra |
| `API_KEY_2` | livre | Chave de API extra |

### Precedência de configuração

Carregado nesta ordem (posterior sobrescreve anterior):

1. `config.global` — defaults globais
2. `secrets.global` — credenciais compartilhadas
3. `bots/<nome>/.env` — overrides do agente
4. `bots/<nome>/secrets.env` — credenciais exclusivas do agente

---

## Ferramentas modulares

Habilite por agente via `TOOLS=` no `.env`. Ferramentas de memória, tarefas e agendamentos estão sempre ativas.

| Ferramenta | Chave | Requer | Funcionalidade |
|---|---|---|---|
| Memória | *(sempre ativa)* | — | Leitura/escrita de memória e estado |
| Tarefas | *(sempre ativa)* | — | Criar, atualizar e listar tarefas persistentes |
| Agendamentos | *(sempre ativa)* | — | Notificações proativas agendadas |
| Shell | `shell` | — | Executa comandos no servidor (com denylist) |
| Cron | `cron` | — | Gerencia crontabs do sistema |
| Arquivos | `files` | — | Leitura/escrita no workspace + envio via Telegram |
| HTTP | `http` | — | Requisições HTTP/REST (com resolução de placeholders) |
| Git | `git` | `GIT_TOKEN`, `GIT_USER`, `GIT_EMAIL` | Clone, commit, push, pull |
| GitHub | `github` | `GITHUB_TOKEN` | API GitHub (issues, PRs, repos, CI) |
| Database | `database` | `DB_URL` | Queries SQL (PostgreSQL, MySQL, SQLite) |

---

## Subagentes

Subagentes são agentes especializados que os agentes pai podem invocar para tarefas específicas. Ficam em `subagents/<nome>/` e são descobertos automaticamente.

```
subagents/criador-graficos/
├── .env        ← Config do subagente
└── soul.md     ← System prompt especializado
```

### Configuração do `.env` do subagente

```env
NAME=Criador de Gráficos
DESCRIPTION=Gera gráficos e visualizações de dados
PROVIDER=openrouter
MODEL=google/gemini-3.1-flash-image-preview
TOOLS=none                     # ferramentas permitidas (ou none)
ALLOWED_PARENTS=*              # quais agentes podem invocar (* = todos)
MODE=simple                    # simple | agentic
```

### Modos de execução

| Modo | Comportamento | Uso |
|---|---|---|
| `simple` | Uma única chamada LLM, sem ferramentas | 95% dos casos — rápido e direto |
| `agentic` | Loop com tool use (até 10 iterações) | Tarefas complexas que precisam de ferramentas |

### Regras

- Subagentes recebem **apenas** as ferramentas declaradas no seu `TOOLS` (não herdam memória, tarefas ou agendamentos do pai)
- Subagentes **não podem invocar outros subagentes** (anti-recursão)
- Analytics registrados separadamente como `<bot>/sub:<agent_name>`
- Credenciais isoladas: `.env` do subagente é parseado sem modificar variáveis globais

---

## Suporte a mídia

| Tipo | Processamento | Limite |
|---|---|---|
| Foto | Visão nativa (Anthropic/OpenRouter) ou descrição via LLM auxiliar (claude-cli) | 20 MB |
| Documento | Extração de texto: TXT/CSV/JSON/PY direto, PDF via pdfplumber, binários → nome+tamanho | 20 MB |
| Vídeo / Video Note | Extração de áudio com ffmpeg → transcrição com Whisper → texto | 20 MB |
| Áudio / Voz | Transcrição com Whisper (modelo `small`, pt-BR) | 20 MB |
| Reply (citação) | Contexto `[Em resposta a "nome": "texto"]` injetado automaticamente | 500 chars |
| Envio de arquivo | Via tool `send_telegram_file`, fila drenada após resposta do Claude | 50 MB |

---

## Exemplos de modelos

```env
# Claude (anthropic / claude-cli)
MODEL=claude-sonnet-4-6
MODEL=claude-opus-4-6
MODEL=claude-haiku-4-5-20251001

# OpenAI (codex)
MODEL=gpt-5.4
MODEL=gpt-5.3-codex-spark
MODEL=gpt-5.3-codex
MODEL=gpt-5.2-codex
MODEL=gpt-5.2
MODEL=gpt-5.1-codex-max
MODEL=gpt-5.1-codex-mini
MODEL=gpt-5.1

# OpenRouter (qualquer modelo disponível)
MODEL=x-ai/grok-3
MODEL=x-ai/grok-3:online           # com busca em tempo real
MODEL=google/gemini-2.0-flash
MODEL=openai/gpt-5.4
MODEL=mistralai/mistral-small-3.1
```

---

## Bug Fixer Agent

Agente autônomo que monitora erros nos logs e analytics, invoca Claude para analisar e notifica o admin via Telegram.

```env
# No config.global:
BUGFIXER_ENABLED=true
BUGFIXER_TIMES_PER_DAY=3           # quantas vezes por dia roda
BUGFIXER_TELEGRAM_TOKEN=<token>     # token para notificações (fallback: token do primeiro agente)
```

Roda via cron, configurável pelo painel admin.

---

## Crons automáticos

| Schedule | Script | Descrição |
|---|---|---|
| `50 23 * * *` | `memory-autosave.sh` | Destila memória diária → MEMORY.md |
| `0 2 * * 0` | `memory-cleanup.sh` | Remove diários com mais de 30 dias |
| Configurável | `bugfixer.py` | Bug Fixer Agent (frequência definida no config.global) |
| `0 8 * * *` | `check-update.sh` | Verifica se há nova versão no remote e notifica admin |
