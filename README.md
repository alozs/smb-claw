# SMB Claw — Multi-Bot AI Framework

Sistema de gestão de agentes de IA para Telegram, focado em **simplicidade**, **facilidade de uso** e **segurança**. Rode múltiplos bots independentes numa única VPS, cada um com personalidade própria, memória persistente, ferramentas modulares e subagentes especializados.

Pensado para quem quer colocar agentes de IA em produção rápido — sem infraestrutura complexa, sem Docker, sem Kubernetes. Um script e você tem um agente funcionando.

### Ultra leve

Todo o sistema — bot engine, painel admin, ferramentas, scheduler, bugfixer — soma **~11.300 linhas de código** (Python + HTML + Shell). Sem frameworks pesados, sem camadas de abstração desnecessárias, sem Docker.

Cada agente consome **~75 MB de RAM** em idle e **zero portas de rede** — usa long polling do Telegram, não precisa abrir porta nem configurar domínio/SSL. A única porta usada é a do painel admin (8080), e ela é opcional.

Rode **10, 20, 50 agentes** na mesma VPS de $5/mês. Eles só processam quando recebem mensagem — sem polling pesado, sem websockets, sem overhead. Uma VPS com 2 GB de RAM já aguenta dezenas de agentes simultâneos sem suar.

## Destaques

- **Ultra leve** — ~75 MB por agente em idle, zero portas abertas, escala em VPS barata
- **Sem limites de agentes** — crie quantos quiser, cada um isolado com seu próprio banco, memória e personalidade
- **Multi-provedor** — Claude (API ou CLI), OpenRouter (Grok, GPT-4o, Gemini), OpenAI (Codex CLI)
- **Memória em camadas** — personalidade, perfil do usuário, memória de longo prazo, diários diários
- **Ferramentas modulares** — shell, HTTP, Git, GitHub, banco de dados, arquivos, cron
- **Subagentes** — delegue tarefas para agentes especializados (geração de imagens, análises etc.)
- **Painel web** — dashboard FastAPI com setup wizard, editor de configuração e logs em tempo real
- **Bug Fixer autônomo** — agente que monitora erros e tenta corrigir sozinho
- **Segurança por padrão** — tokens isolados, sandbox de comandos, controle de acesso por aprovação

---

## Requisitos

- **Python 3.10+**
- **Linux com systemd** (Ubuntu/Debian recomendado)
- **Token do Telegram** via [@BotFather](https://t.me/BotFather) (um por agente)
- **Provedor de IA** (pelo menos um):
  - [Claude Code](https://claude.ai) — assinatura ativa (sem API key, usa OAuth)
  - [Anthropic API](https://console.anthropic.com) — API key
  - [OpenRouter](https://openrouter.ai) — API key (acesso a Grok, GPT-4o, Gemini, Mistral etc.)
  - [OpenAI/Codex](https://platform.openai.com) — API key ou Codex CLI OAuth

### Dependências opcionais

| Dependência | Para quê |
|---|---|
| `ffmpeg` + `openai-whisper` | Transcrição de áudio e vídeo |
| `pdfplumber` | Extração de texto de PDFs |
| `Node.js` | Claude Code CLI e Codex CLI |

---

## Instalação

```bash
git clone https://github.com/seu-usuario/claude-bots.git
cd claude-bots
bash setup.sh
```

O `setup.sh` faz tudo automaticamente:
1. Verifica Python, Node, ffmpeg, Claude CLI, Codex CLI
2. Detecta autenticações já configuradas (OAuth, API keys)
3. Instala pacotes Python faltantes
4. Inicia o painel admin com o setup wizard

Ao final, abra o navegador no endereço exibido (padrão: `http://<ip>:8080`).

### Setup wizard (painel web)

Na primeira execução, o painel abre o **wizard de configuração** onde você:
- Escolhe o provedor de IA (Claude CLI, Anthropic, OpenRouter, Codex)
- Testa a conexão com o provedor
- Define o `ADMIN_ID` (seu Telegram ID) e modelo padrão
- Cria seu primeiro bot

### Instalação manual (sem wizard)

```bash
pip install -r requirements.txt
cp config.global.example config.global
nano config.global              # preencha ADMIN_ID, PROVIDER, MODEL
nano secrets.global             # API keys (se aplicável)
chmod 600 secrets.global
```

---

## Criando um agente

### Via painel web

Acesse o dashboard → botão **"Novo Bot"** → preencha nome, token e personalidade.

### Via terminal

```bash
bash criar-bot.sh meu-agente
```

O script cria toda a estrutura e registra o serviço systemd. Depois:

1. Obtenha o token no [@BotFather](https://t.me/BotFather)
2. Configure o token:
   ```bash
   nano bots/meu-agente/.env
   # TELEGRAM_TOKEN=<token>
   ```
3. Defina a personalidade:
   ```bash
   nano bots/meu-agente/soul.md
   ```
4. (Opcional) Configure credenciais sensíveis:
   ```bash
   bash configurar-secrets.sh meu-agente
   ```
5. Inicie:
   ```bash
   sudo systemctl start claude-bot-meu-agente
   sudo systemctl enable claude-bot-meu-agente
   ```

---

## Estrutura de um agente

```
bots/meu-agente/
├── .env              ← Token, modelo, ferramentas, provedor
├── secrets.env       ← Credenciais sensíveis (chmod 600)
├── soul.md           ← Personalidade e instruções
├── USER.md           ← Perfil do usuário (contexto pessoal)
├── MEMORY.md         ← Memória de longo prazo (auto-destilada toda noite)
├── memory/           ← Diários diários (auto-gerados)
│   └── 2026-03-15.md
├── workspace/        ← Sandbox de arquivos
├── avatar.jpeg       ← Avatar do bot (opcional)
└── bot_data.db       ← SQLite: conversas, tarefas, analytics
```

### Configuração do `.env`

```env
TELEGRAM_TOKEN=seu_token_aqui
BOT_NAME=Meu Agente
MAX_HISTORY=20

# Ferramentas: none | shell,cron,files,http,git,github,database
TOOLS=shell,http,files

# Provedor (herda do config.global se omitido)
PROVIDER=claude-cli

# Modelo (herda do config.global se omitido)
# MODEL=claude-sonnet-4-6

# Acesso: open | approval | closed
ACCESS_MODE=approval

# Comportamento em grupos: always | mention_only
GROUP_MODE=always

# Descrição curta (exibida no painel admin)
DESCRIPTION=Meu agente pessoal
```

### Precedência de configuração

Carregado nesta ordem (posterior sobrescreve anterior):

1. `config.global` — defaults globais
2. `secrets.global` — credenciais compartilhadas
3. `bots/<nome>/.env` — overrides do agente
4. `bots/<nome>/secrets.env` — credenciais exclusivas do agente

---

## Provedores de IA

O framework suporta **4 provedores** com autenticação flexível — API key ou OAuth automático.

### `claude-cli` — Recomendado

> Usa a assinatura do Claude Code. **Sem custo extra de API**, sem API key. Token OAuth renovado automaticamente.

```
┌─────────────────────────────────────────────────────────┐
│  Provedor:   claude-cli                                 │
│  Auth:       OAuth automático (~/.claude/.credentials)   │
│  API key:    NÃO precisa                                │
│  Modelos:    Claude Opus, Sonnet, Haiku                 │
│  Ferramentas: tools nativas do Claude Code (Bash, Read…)│
└─────────────────────────────────────────────────────────┘
```

**Como configurar:**
```bash
# 1. Instale o Claude Code CLI
npm install -g @anthropic-ai/claude-code

# 2. Faça login (abre navegador para autenticar)
claude login

# 3. Configure o provedor
# config.global:
PROVIDER=claude-cli
MODEL=claude-sonnet-4-6
```

O token OAuth fica em `~/.claude/.credentials.json` e é lido a cada chamada — renovação automática pelo CLI sem precisar reiniciar os bots.

### `anthropic` — API direta

> API key da Anthropic ou OAuth do Claude Code. Controle total de custos via console.

```
┌─────────────────────────────────────────────────────────┐
│  Provedor:   anthropic                                  │
│  Auth:       API key OU OAuth do Claude Code            │
│  Modelos:    Claude Opus, Sonnet, Haiku                 │
│  Ferramentas: tools customizadas do framework           │
└─────────────────────────────────────────────────────────┘
```

**Opção A — API key:**
```bash
# secrets.global:
ANTHROPIC_API_KEY=sk-ant-api03-...

# config.global:
PROVIDER=anthropic
MODEL=claude-sonnet-4-6
```

**Opção B — OAuth do Claude Code (sem API key):**
```bash
# Basta ter feito `claude login` — o framework detecta automaticamente
# ~/.claude/.credentials.json é lido a cada chamada
# config.global:
PROVIDER=anthropic
MODEL=claude-sonnet-4-6
# (sem ANTHROPIC_API_KEY — usa OAuth)
```

### `codex` — OpenAI / ChatGPT

> Modelos OpenAI via API key ou OAuth do Codex CLI (ChatGPT). Ideal para GPT-5.x e família codex.

```
┌─────────────────────────────────────────────────────────┐
│  Provedor:   codex                                      │
│  Auth:       API key OU OAuth do Codex CLI (ChatGPT)    │
│  Modelos:    GPT-5.4, GPT-5.3-codex, GPT-5.2, GPT-5.1  │
│  Ferramentas: tools customizadas do framework           │
└─────────────────────────────────────────────────────────┘
```

**Opção A — API key:**
```bash
# secrets.global:
OPENAI_API_KEY=sk-...

# config.global:
PROVIDER=codex
MODEL=gpt-5.4
```

**Opção B — OAuth do Codex CLI (sem API key):**
```bash
# 1. Instale o Codex CLI
npm install -g @openai/codex

# 2. Faça login (autentica via ChatGPT/OpenAI)
codex login

# 3. Configure — o token fica em ~/.codex/auth.json
# config.global:
PROVIDER=codex
MODEL=gpt-5.4
# (sem OPENAI_API_KEY — usa OAuth)
```

O token OAuth é lido a cada chamada — renovação automática pelo Codex CLI.

### `openrouter` — Multi-modelo

> Acesso a dezenas de modelos via uma única API key. Grok, GPT-4o, Gemini, Mistral, LLaMA e mais.

```
┌─────────────────────────────────────────────────────────┐
│  Provedor:   openrouter                                 │
│  Auth:       API key (obrigatória)                      │
│  Modelos:    Grok 3, GPT-4o, Gemini, Mistral, LLaMA…   │
│  Ferramentas: tools customizadas do framework           │
└─────────────────────────────────────────────────────────┘
```

```bash
# secrets.global:
OPENROUTER_API_KEY=sk-or-v1-...

# config.global:
PROVIDER=openrouter
MODEL=x-ai/grok-3
```

### Resumo de autenticação

| Provedor | API key | OAuth CLI | Custo |
|:---|:---:|:---:|:---|
| `claude-cli` | — | `claude login` | Incluso na assinatura Claude Code |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude login` | Pay-per-use (Anthropic) |
| `codex` | `OPENAI_API_KEY` | `codex login` | Pay-per-use (OpenAI) |
| `openrouter` | `OPENROUTER_API_KEY` | — | Pay-per-use (OpenRouter) |

> **Dica:** OAuth é sempre prioridade menor que API key. Se `ANTHROPIC_API_KEY` estiver definida, ela é usada mesmo com OAuth disponível. Remova a key para forçar OAuth.

### Exemplos de modelos

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

Subagentes são agentes especializados que os bots pai podem invocar para tarefas específicas. Ficam em `subagents/<nome>/` e são descobertos automaticamente.

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
ALLOWED_PARENTS=*              # quais bots podem invocar (* = todos)
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

## Sistema de memória

O system prompt é **reconstruído a cada mensagem** — mudanças nos arquivos têm efeito imediato.

| Camada | Arquivo | Descrição |
|---|---|---|
| Global | `context.global` | Instruções compartilhadas por todos os bots |
| Personalidade | `soul.md` | Papel, especialidade e regras do agente |
| Perfil do usuário | `USER.md` | Contexto pessoal, preferências, projetos |
| Longo prazo | `MEMORY.md` | Fatos destilados (auto-atualizado toda noite) |
| Diário de hoje | `memory/YYYY-MM-DD.md` | Eventos registrados pelo bot durante o dia |
| Diário de ontem | `memory/YYYY-MM-DD.md` | Continuidade da conversa do dia anterior |

### Destilação automática

- **23:50 diariamente**: `memory-autosave.sh` lê os diários e sessões do dia, chama um LLM para extrair fatos duráveis e atualiza o `MEMORY.md`
- **02:00 aos domingos**: `memory-cleanup.sh` remove diários com mais de 30 dias

---

## Painel admin (web)

Dashboard FastAPI para gerenciar todo o sistema via navegador.

```bash
# Já iniciado pelo setup.sh, ou manualmente:
bash setup.sh
# Acesse http://<ip>:8080
```

### Funcionalidades

- **Dashboard** — visão geral de todos os bots (status, uptime)
- **Setup wizard** — configuração guiada na primeira execução
- **Editor de configuração** — `.env`, `soul.md`, `USER.md`, `MEMORY.md`, `context.global`, `config.global`
- **Gerenciamento de serviços** — start, stop, restart via interface
- **Logs em tempo real** — streaming de journalctl no navegador
- **Criação/exclusão de bots** — com validação e setup automático do systemd
- **Upload de avatar** — imagem personalizada por bot
- **Gerenciamento de usuários** — aprovados, analytics, conversas
- **Agendamentos** — criar, listar e remover notificações proativas
- **Envio de mensagens** — enviar mensagem direta ou broadcast para todos os usuários
- **Exportação** — download de conversas e dados
- **Subagentes** — criar, editar e remover subagentes
- **Bug Fixer** — configurar, executar e ver logs do agente autônomo
- **Crontab** — editor visual do crontab do sistema

### API REST

Todas as operações são expostas como API REST em `/api/`:

| Endpoint | Método | Descrição |
|---|---|---|
| `/api/bots` | GET | Lista todos os bots |
| `/api/bots` | POST | Cria novo bot |
| `/api/bots/{name}` | GET/DELETE | Detalhes / remove bot |
| `/api/bots/{name}/env` | GET/PUT | Configuração .env |
| `/api/bots/{name}/file/{fname}` | GET/PUT | Editar soul.md, USER.md etc. |
| `/api/bots/{name}/action` | POST | start/stop/restart serviço |
| `/api/bots/{name}/logs` | GET | Stream de logs (SSE) |
| `/api/bots/{name}/analytics` | GET | Métricas de uso |
| `/api/bots/{name}/users` | GET | Usuários aprovados |
| `/api/bots/{name}/schedules` | GET/POST/DELETE | Agendamentos |
| `/api/bots/{name}/send-message` | POST | Enviar mensagem direta |
| `/api/bots/{name}/broadcast` | POST | Broadcast para todos |
| `/api/bots/{name}/avatar` | GET/POST/DELETE | Avatar do bot |
| `/api/global/{fname}` | GET/PUT | Arquivos globais |
| `/api/subagents` | GET/POST/DELETE | Gerenciar subagentes |
| `/api/setup/*` | POST | Setup wizard |
| `/api/system/bugfixer` | GET/PUT/POST | Bug Fixer Agent |
| `/api/crontab` | GET/PUT | Crontab do sistema |

---

## Comandos Telegram

### Todos os usuários

| Comando | Descrição |
|---|---|
| `/start` | Inicia sessão, limpa histórico |
| `/clear` | Limpa histórico de conversa |
| `/info` | Exibe o soul.md do bot |
| `/id` | Retorna o Telegram ID do usuário |
| `/tasks [status]` | Lista tarefas (filtros: all, in_progress, paused, completed, failed, cancelled) |

### Somente admin

| Comando | Descrição |
|---|---|
| `/users` | Lista usuários aprovados |
| `/pending` | Lista solicitações pendentes |
| `/revoke <id>` | Revoga acesso de um usuário |
| `/memory` | Mostra status dos arquivos de memória |
| `/stats [período]` | Analytics: tokens, custo, mensagens (hoje/semana/mes/N) |
| `/restart` | Reinicia o bot |
| `/status` | Status do sistema |
| `/version` | Versão atual e atualizações pendentes |
| `/update` | Puxa atualizações do remote e reinicia serviços |

### Modos de acesso

| Modo | Comportamento |
|---|---|
| `open` | Qualquer pessoa pode usar |
| `approval` | Admin aprova novos usuários via botões inline (padrão) |
| `closed` | Apenas usuários pré-aprovados |

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

## Gerenciamento de serviços

```bash
# Via script
bash gerenciar.sh status                 # status de todos
bash gerenciar.sh list                   # listar bots disponíveis
bash gerenciar.sh start meu-agente       # iniciar um
bash gerenciar.sh start                  # iniciar todos
bash gerenciar.sh stop meu-agente        # parar um
bash gerenciar.sh restart meu-agente     # reiniciar
bash gerenciar.sh logs meu-agente        # logs em tempo real

# Via systemctl
sudo systemctl start claude-bot-meu-agente
sudo systemctl stop claude-bot-meu-agente
sudo journalctl -u claude-bot-meu-agente -f
```

Cada bot roda como um serviço systemd com `Restart=always` e `RestartSec=10` — se cair, volta sozinho.

---

## Bug Fixer Agent

Agente autônomo que monitora erros nos logs e analytics, invoca Claude para analisar e notifica o admin via Telegram.

```env
# No config.global:
BUGFIXER_ENABLED=true
BUGFIXER_TIMES_PER_DAY=3           # quantas vezes por dia roda
BUGFIXER_TELEGRAM_TOKEN=<token>     # token para notificações (fallback: token do primeiro bot)
```

Roda via cron, configurável pelo painel admin.

---

## Múltiplos bots na mesma VPS

Crie quantos agentes quiser — **não existe limite no framework**. O único limite é a RAM e CPU da sua VPS.

| VPS | RAM | Agentes confortáveis |
|---|---|---|
| $5/mês | 1 GB | ~10 agentes |
| $10/mês | 2 GB | ~25 agentes |
| $20/mês | 4 GB | ~50 agentes |

Cada agente usa **long polling** do Telegram — **zero portas abertas**, sem domínio, sem SSL, sem nginx. O Telegram cuida de toda a conectividade. A única porta usada (opcionalmente) é a do painel admin (8080).

Em idle, cada agente consome **~75 MB de RAM** e **0% de CPU**. Só processa quando recebe mensagem. Sem polling pesado, sem websockets, sem overhead de container.

**Compartilhado** entre todos os bots:
- `config.global` / `secrets.global` — chaves de API, admin ID
- `context.global` — instruções globais
- `bot.py`, `db.py`, `tools/` — código-fonte
- `subagents/` — subagentes (com controle de acesso via `ALLOWED_PARENTS`)

**Isolado** por bot:
- `.env` / `secrets.env` — token e credenciais
- `soul.md` — personalidade
- `bot_data.db` — conversas, tarefas, analytics
- `workspace/` — sandbox de arquivos
- `memory/` — memória diária e de longo prazo

---

## Segurança

- **Tokens isolados** — cada bot tem token único, com lock file que impede instâncias duplicadas
- **Credenciais protegidas** — `.env`, `secrets.env`, `secrets.global` com `chmod 600`
- **Shell sandbox** — denylist bloqueia `printenv`, `env`, `$ANTHROPIC_API_KEY`, `$TELEGRAM_TOKEN` etc.
- **Path traversal** — file tool sandboxado no `WORK_DIR` do bot
- **HTTP safety** — bloqueia IPs internos (169.254.x, localhost, metadata endpoints); resolve placeholders `$VAR` nos headers sem expor secrets
- **SQL safety** — bloqueia DROP/TRUNCATE sem WHERE
- **Git safety** — token injetado no URL em memória, nunca exibido nos logs
- **Markdown safety** — dados dinâmicos escapados para evitar erros de parse do Telegram
- **Controle de acesso** — modo `approval` (padrão) exige aprovação do admin para novos usuários
- **Drop pending updates** — mensagens acumuladas durante downtime são descartadas no restart

---

## Arquitetura

```
claude-bots/
├── bot.py                  # Core: handlers, loop principal, integração Telegram
├── db.py                   # Persistência SQLite (WAL mode)
├── scheduler.py            # Notificações proativas agendadas (loop 60s)
├── security.py             # Sandbox de shell, proteção path traversal
├── bugfixer.py             # Agente autônomo de correção de erros
│
├── tools/                  # Ferramentas modulares
│   ├── __init__.py         # Registry + dispatcher central
│   ├── memory.py           # memory_write, memory_read, state_rw
│   ├── tasks.py            # task_create, task_update, task_list
│   ├── schedule.py         # Agendamentos via SQLite
│   ├── shell.py            # Shell, cron, file operations
│   ├── http.py             # HTTP requests + placeholder resolution
│   ├── git.py              # Git operations
│   ├── github_tool.py      # GitHub API REST v3
│   ├── database.py         # SQL queries
│   ├── agent.py            # Subagentes (build_definitions + execute)
│   └── telegram_file.py    # Envio de arquivos via Telegram
│
├── admin/                  # Painel web de administração
│   ├── app.py              # FastAPI (60+ endpoints REST)
│   ├── templates/          # HTML (Jinja2)
│   └── static/             # Assets (ícones, logos, JS)
│
├── bots/                   # Instâncias dos agentes
│   └── <nome>/             # Um diretório por bot
│
├── subagents/              # Subagentes especializados
│   └── <nome>/             # Um diretório por subagente
│
├── config.global           # Configurações globais
├── config.global.example   # Template de configuração
├── secrets.global          # Credenciais globais (chmod 600)
├── context.global          # Instruções de sistema globais
│
├── setup.sh                # Bootstrap: verifica deps, inicia painel admin
├── criar-bot.sh            # Cria novo bot com toda infraestrutura + systemd
├── gerenciar.sh            # Gerencia serviços (start/stop/restart/logs)
├── configurar-secrets.sh   # Entrada segura de credenciais
├── memory-autosave.sh      # Destilação diária de memória (cron 23:50)
├── memory-cleanup.sh       # Limpeza semanal de diários antigos (cron domingo 02:00)
│
├── VERSION                 # Versão atual (semver)
├── CHANGELOG.md            # Histórico de mudanças (auto-gerado)
├── release.sh              # Bump versão, changelog, tag, push, notificação
├── update.sh               # Pull + restart de serviços
├── check-update.sh         # Cron diário: verifica updates pendentes
│
├── tests/                  # Testes (pytest)
├── logs/                   # Logs do bugfixer e memory
├── .locks/                 # Lock files por token (anti-duplicata)
└── requirements.txt        # Dependências Python
```

---

## Concorrência e resiliência

- **Lock per-user** — cada usuário tem seu próprio `asyncio.Lock()`, mensagens processadas em sequência
- **Usuários diferentes** — processados em paralelo sem contenção
- **SQLite WAL** — leituras concorrentes + escritas atômicas
- **Retry automático** — cliente Anthropic com `max_retries=3` e backoff
- **Startup recovery** — tarefas `in_progress` viram `paused` no boot, usuário notificado com botões Retomar/Cancelar
- **Lock de token** — impede duas instâncias com o mesmo `TELEGRAM_TOKEN`
- **Persistência total** — conversas, tarefas e analytics no SQLite, sobrevivem restarts e crashes

---

## Crons automáticos

| Schedule | Script | Descrição |
|---|---|---|
| `50 23 * * *` | `memory-autosave.sh` | Destila memória diária → MEMORY.md |
| `0 2 * * 0` | `memory-cleanup.sh` | Remove diários com mais de 30 dias |
| Configurável | `bugfixer.py` | Bug Fixer Agent (frequência definida no config.global) |
| `0 8 * * *` | `check-update.sh` | Verifica se há nova versão no remote e notifica admin |

---

## Versionamento e atualizações

O sistema usa **versionamento semântico** (`MAJOR.MINOR.PATCH`) com release automatizado.

### Criar um release

```bash
./release.sh
```

Faz tudo automaticamente:
1. Bumpa a versão (patch)
2. Gera changelog a partir dos commits
3. Commita, cria tag e pusha
4. Notifica o admin no Telegram

### Atualizar uma instância

Via Telegram (em qualquer bot):
```
/version    → mostra versão atual e se há updates
/update     → puxa atualizações e reinicia tudo
```

Via terminal:
```bash
./update.sh
```

### Verificação automática

O cron `check-update.sh` roda diariamente às 8h e notifica o admin no Telegram se houver commits novos no `origin/main`.

### Arquivos

| Arquivo | Papel |
|---|---|
| `VERSION` | Versão atual (ex: `0.1.2`) |
| `CHANGELOG.md` | Histórico de mudanças por versão |
| `release.sh` | Bump, changelog, tag, push, notificação |
| `update.sh` | Pull + restart de serviços |
| `check-update.sh` | Cron: verifica updates pendentes |

---

## Testes

```bash
cd /home/ubuntu/claude-bots
pytest tests/ -v
```

| Arquivo | Cobertura |
|---|---|
| `tests/test_security.py` | Shell denylist, path traversal, SQL safety, HTTP placeholders |
| `tests/test_config.py` | Carregamento de .env, precedência de config |
| `tests/test_analytics.py` | Analytics, persistência de conversas, schedules |

---

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

### .env do bot

| Variável | Obrigatório | Descrição |
|---|---|---|
| `TELEGRAM_TOKEN` | sim | Token do @BotFather |
| `BOT_NAME` | — | Nome do bot (padrão: nome da pasta) |
| `MAX_HISTORY` | — | Máx. mensagens no histórico (padrão: 20) |
| `TOOLS` | — | Ferramentas ativas (padrão: none) |
| `WORK_DIR` | — | Sandbox de arquivos (padrão: `<bot>/workspace`) |
| `MODEL` | — | Override do modelo |
| `PROVIDER` | — | Override do provedor |
| `ACCESS_MODE` | — | Override do modo de acesso |
| `GROUP_MODE` | — | `always` ou `mention_only` (para grupos) |
| `DESCRIPTION` | — | Descrição curta (exibida no painel) |

### secrets.env do bot

| Variável | Ferramenta | Descrição |
|---|---|---|
| `DB_URL` | `database` | Connection string do banco |
| `GIT_TOKEN` | `git` | Token GitHub/GitLab |
| `GIT_USER` | `git` | Username git |
| `GIT_EMAIL` | `git` | Email git |
| `GITHUB_TOKEN` | `github` | Token GitHub (fallback: `GIT_TOKEN`) |
| `API_KEY_1` | livre | Chave de API extra |
| `API_KEY_2` | livre | Chave de API extra |

---

## Licença

MIT
