# Claude Bots — Guia de Manutenção

Este arquivo é lido pelo Claude Code antes de qualquer modificação no projeto.
**Sempre que algo mudar, este arquivo deve ser atualizado na mesma sessão.**

---

## Arquitetura

```
bot.py          — Core: config, Telegram handlers, main loop
db.py           — Persistência SQLite (WAL mode) — conversations, tasks, schedules, analytics, approved_users, traces, action_log
tracer.py       — Tracing granular: Trace/Span por invocação de ask_*, persistido em traces
compactor.py    — Compactação inteligente de contexto via sumarização (opt-in)
security.py     — Shell safety, path traversal, output sanitization, detect_injection()
scheduler.py    — Loop background de notificações proativas + cleanup diário do action_log
guardrails.py   — Classificação de risco de ações (safe/moderate/dangerous) + tool request_approval
tools/          — Ferramentas modulares
  __init__.py   — Registry + dispatcher central
  memory.py     — memory_write, memory_read, state_rw
  tasks.py      — task_create, task_update, task_list
  shell.py      — run_shell, manage_cron, manage_files
  http.py       — http_request
  git.py        — git_op
  github_tool.py — github (API REST v3)
  database.py   — db_query
  schedule.py   — schedule (add/list/remove)
  agent.py      — sub-agentes (agent_*): build_definitions + execute_sync
subagents/      — NOVO: diretório de sub-agentes especializados
  <nome>/
    .env        — NAME, DESCRIPTION, PROVIDER, MODEL, TOOLS, ALLOWED_PARENTS
    soul.md     — system prompt do sub-agente
```

## Arquivos de suporte

| Arquivo | Papel |
|---|---|
| `setup.sh` | **Bootstrap mínimo** — instala deps, sobe painel admin; onboarding completo é no painel web |
| `criar-bot.sh` | Cria novo bot — deve refletir 100% do que `bot.py` suporta |
| `config.global` | Fonte de verdade em runtime: carregado por `bot.py` como defaults globais |
| `secrets.global` | Credenciais sensíveis compartilhadas por todos os bots (chmod 600) |
| `configurar-secrets.sh` | Entrada segura de credenciais sensíveis |
| `gerenciar.sh` | Controle de serviços (start/stop/logs) |
| `context.global` | Instruções globais injetadas no system prompt de todos os bots |
| `memory-autosave.sh` | Destila memória diária → MEMORY.md (cron 23:50). Fallback de provedor: Claude OAuth → Codex OAuth → OPENROUTER_API_KEY → OPENAI_API_KEY. Salva estado em `.memory_autosave_state`. |
| `memory-cleanup.sh` | Remove diários antigos (cron domingo 02:00) |
| `behavior-extract.sh` | Extrai/atualiza BEHAVIOR.md com perfil comportamental (cron 23:55). Substitui documento inteiro com merge inteligente. Respeita `BEHAVIOR_MAX_CHARS`. Só roda bots com `BEHAVIOR_LEARNING_ENABLED=true`. |
| `bugfixer.py` | Bug Fixer Agent — detecta erros via analytics + journalctl, invoca Claude para corrigir, notifica admin via Telegram |
| `VERSION` | Versão atual do sistema (semver: MAJOR.MINOR.PATCH) |
| `CHANGELOG.md` | Histórico de mudanças por versão (auto-gerado pelo `release.sh`) |
| `release.sh` | Bump de versão, changelog, commit, tag, push e notificação Telegram |
| `update.sh` | Pull do remote + **migrate-env.sh** + restart de serviços um a um |
| `migrate-env.sh` | Adiciona variáveis novas ao `.env` de cada bot existente (idempotente). Chamado automaticamente pelo `update.sh`. Respeita valores já definidos (inclusive comentados). |
| `check-update.sh` | Cron diário: verifica se origin/main tem commits novos e notifica admin |

---

## Estrutura de cada bot

```
bots/<nome>/
├── .env              ← config pública (token, model, tools, admin)
├── secrets.env       ← credenciais sensíveis (DB, git, GitHub, APIs)
├── soul.md           ← personalidade e instruções do bot
├── USER.md           ← perfil do usuário / preferências
├── MEMORY.md         ← memória de longo prazo destilada
├── memory/           ← diários diários YYYY-MM-DD.md
│   └── YYYY-MM-DD.md
├── BEHAVIOR.md       ← perfil comportamental (auto-gerado pelo behavior-extract.sh, opt-in)
├── workspace/        ← sandbox do file tool
└── bot_data.db       ← SQLite: conversations, tasks, schedules, analytics, approved_users, action_log
```

---

## Persistência (SQLite)

Toda persistência usa `db.py` (classe `BotDB`). O banco é `bots/<nome>/bot_data.db` com WAL mode.

| Tabela | Substitui | Descrição |
|---|---|---|
| `conversations` | conversations.json | Histórico de conversas ativo (sessão atual) por user |
| `sessions_archive` | — | Histórico completo de sessões arquivadas no /start e /clear |
| `traces` | — | Traces detalhados por invocação LLM (spans, tokens, latência por tool call) |
| `tasks` | tasks.json | Tarefas persistentes |
| `schedules` | schedules.json | Agendamentos proativos |
| `analytics` | analytics.jsonl | Log de uso (tokens, latência, erros) |
| `approved_users` | approved_users.json | Usuários aprovados |
| `action_log` | — | Log de auditoria de tool calls com classificação de risco (guardrails). Cleanup automático: 30 dias. |

**Migração automática:** No boot, `db.migrate_from_json()` importa arquivos JSON legados (se existirem) e renomeia para `.bak`.

---

## Concorrência

- **Lock per-user:** Cada user tem seu próprio `asyncio.Lock()`. Mensagens do mesmo user são processadas em sequência (append user msg → ask_claude → append reply → save DB — tudo dentro do lock).
- **Users diferentes:** Processados em paralelo sem contenção.
- **Scheduler:** Também adquire o lock do user antes de manipular conversas.
- **Erro no ask_claude:** A msg do user que causou erro é removida do histórico para não poluir.

---

## Sistema de memória (camadas)

| Camada | Arquivo | Quando é carregado |
|---|---|---|
| Global | `context.global` | Sempre (antes do soul.md, vale para todos os bots) |
| Personalidade | `soul.md` | Sempre (base do system prompt) |
| Perfil usuário | `USER.md` | Sempre |
| Longo prazo | `MEMORY.md` | Sempre |
| Hoje | `memory/YYYY-MM-DD.md` | Sempre |
| Ontem | `memory/YYYY-MM-DD.md` | Sempre (continuidade) |
| Comportamental | `BEHAVIOR.md` | Quando `BEHAVIOR_LEARNING_ENABLED=true` (truncado em `BEHAVIOR_MAX_CHARS`) |

O system prompt é **reconstruído a cada mensagem** — mudanças nos arquivos de memória têm efeito imediato.

---

## Ferramentas

Controladas pela variável `TOOLS` no `.env` de cada bot.
Valor: lista separada por vírgula ou `none`.

| Ferramenta | Chave no TOOLS | Requer em secrets.env | Descrição |
|---|---|---|---|
| Memória | *(sempre ativa)* | — | `memory_write`, `memory_read`, `state_rw` |
| Tarefas | *(sempre ativa)* | — | `task_create`, `task_update`, `task_list` |
| Schedule | *(sempre ativa)* | — | Agendamentos de notificações proativas. Campos: `hour`, `minute`, `weekdays`, `message`, `name` (nome curto legível, ex: "Briefing IA"), `description` (o que o agendamento faz). Sempre preencher `name` e `description` ao criar. |
| Aprovação | *(auto — confirm mode)* | — | `request_approval` — pede confirmação ao usuário antes de ação sensível. Ativo quando `GUARDRAILS_MODE=confirm`. |
| Voz | *(sempre ativa)* | `ffmpeg` + `openai-whisper` instalados | Transcreve áudios/voz via Whisper `small` (pt) e processa como texto |
| Shell | `shell` | — | Executa comandos na VPS (com denylist de segurança) |
| Cron | `cron` | — | Gerencia cron jobs |
| Arquivos | `files` | — | Read/write no workspace isolado + `send_telegram_file` para enviar arquivos ao usuário |
| HTTP | `http` | — | Requisições a APIs externas |
| Git | `git` | `GIT_TOKEN`, `GIT_USER`, `GIT_EMAIL` | Clone/push/pull com token injetado |
| GitHub | `github` | `GITHUB_TOKEN` (ou `GIT_TOKEN`) | API do GitHub: PRs, issues, reviews, CI checks |
| Database | `database` | `DB_URL` | Queries SQL (PostgreSQL, MySQL, SQLite) |
| Sub-agentes | *(auto-descoberto)* | — | Delegação para sub-agentes em `subagents/`. Ferramentas `agent_<nome>` geradas automaticamente. Configurar via `subagents/<nome>/.env` e `soul.md`. Sub-agentes recebem **apenas** as ferramentas declaradas no seu `TOOLS` (sem ferramentas "sempre ativas" como tasks/memory/schedule). Analytics de sub-agentes são logados com bot `<bot>/sub:<agent_name>`. |

---

## Suporte a mídia (foto, documento, vídeo, reply)

Implementado em `bot.py`. Todos os tipos são tratados como input válido.

| Tipo | Handler | Comportamento |
|---|---|---|
| Foto | `handle_photo` | `anthropic`/`openrouter`: visão nativa (base64). `claude-cli`: descreve via Haiku/Gemini e injeta texto. Limite: 20 MB |
| Documento | `handle_document` | Extrai texto: `.txt/.csv/.json/.py/…` direto (cap 8000 chars), `.pdf` via pdfplumber (soft dep), binários → nome+tamanho. Limite: 20 MB |
| Vídeo / Video Note | `handle_video` | Extrai áudio com ffmpeg → transcreve com Whisper → mostra `🎬 texto`. Limite: 20 MB |
| Reply (citação) | `_extract_reply_context` | Prefixo `[Em resposta a "nome": "texto"]` injetado em todos os handlers. Truncado em 500 chars |

### Envio de arquivos ao usuário (`send_telegram_file`)

- Disponível quando `files` está em `TOOLS`
- O tool adiciona o arquivo a `_pending_files[user_id]`
- Após `ask_claude()` retornar com sucesso, `_process_message` drena a fila e envia via `send_document`
- Em caso de erro no `ask_claude`, a fila é esvaziada para não vazar entre turnos
- Limite: 50 MB (limite Telegram para `send_document`)

### pdfplumber

Dependência soft — instalada com `pip install pdfplumber --break-system-packages`.
Se não estiver instalada, PDFs recebem fallback graceful com nome+tamanho.

---

## Checklist: ao adicionar uma nova ferramenta

- [ ] `tools/<nome>.py` — criar módulo com `DEFINITIONS` (ou `get_definitions()`) + `execute()`
- [ ] `tools/__init__.py` — importar módulo, adicionar em `build_definitions()` e `execute()`
- [ ] `criar-bot.sh` — adicionar a ferramenta no comentário da linha `TOOLS=` do `.env` template
- [ ] `configurar-secrets.sh` — se precisar de credencial nova, adicionar o `read_secret` correspondente
- [ ] `CLAUDE.md` (este arquivo) — adicionar linha na tabela de ferramentas acima
- [ ] `config.global` — se houver novo valor global, adicionar lá

## Checklist: ao adicionar variáveis de configuração novas (`.env` por bot)

- [ ] `bot.py` — ler a variável com `os.environ.get("MINHA_VAR", "default_conservador")`
- [ ] `migrate-env.sh` — adicionar no array `MIGRATIONS` com comentário de bloco e default conservador
- [ ] `criar-bot.sh` — adicionar comentada no template do `.env` (com explicação)
- [ ] `CLAUDE.md` — adicionar linha na tabela "Variáveis do .env"

> **Regras do migrate-env.sh:**
> - Default sempre conservador (`false`, `0`, valor mais silencioso)
> - Nunca remover entradas antigas do array — idempotência depende disso
> - Comentário de bloco obrigatório antes de cada grupo novo

## Checklist: ao adicionar novo arquivo de contexto/memória

- [ ] `bot.py` — adicionar leitura em `build_context()`
- [ ] `criar-bot.sh` — criar o arquivo template no novo bot
- [ ] `CLAUDE.md` — atualizar a tabela de camadas de memória acima

> **context.global** é um arquivo especial: fica em `BASE_DIR` (não dentro de nenhum bot)
> e é carregado automaticamente por todos os bots. Use-o para instruções que devem valer
> para todo o sistema sem precisar editar o `soul.md` de cada bot individualmente.

## Checklist: ao adicionar novo comando Telegram

- [ ] `bot.py` — implementar handler + registrar em `app.add_handler()`
- [ ] `cmd_start()` em `bot.py` — adicionar o comando na mensagem de boas-vindas
- [ ] `CLAUDE.md` — documentar abaixo

---

## Comandos Telegram disponíveis

### Todos os usuários
| Comando | Descrição |
|---|---|
| `/start` | Inicia sessão, limpa histórico |
| `/clear` | Limpa histórico de conversa |
| `/info` | Exibe soul.md do bot |
| `/id` | Retorna o Telegram ID do usuário |
| `/tasks [status]` | Lista tarefas do agente e agendamentos ativos (filtros de tarefas: all, in_progress, paused, completed, failed, cancelled) |
| `/thinking [off\|low\|medium\|high]` | Define nível de raciocínio estendido por sessão. `off`=desativado (padrão), `low`=2k tokens, `medium`=6k tokens, `high`=16k tokens. Suportado nos providers `anthropic` e `claude-cli`. |

### Somente admin (ADMIN_ID)
| Comando | Descrição |
|---|---|
| `/users` | Lista usuários aprovados |
| `/pending` | Lista solicitações pendentes |
| `/revoke <id>` | Revoga acesso de um usuário |
| `/memory` | Mostra status dos arquivos de memória |
| `/stats [período]` | Analytics: tokens, custo, mensagens (hoje/semana/mes/N) |
| `/trace [id]` | Lista traces recentes ou detalha trace específico (LLM calls, tool calls, timings) |
| `/version` | Mostra versão atual e se há atualizações pendentes |
| `/update` | Puxa atualizações do remote e reinicia todos os serviços |
| `/painel [min]` | Gera link temporário de acesso ao painel admin (default: 30 min) |
| `/config` | Redireciona ao painel web admin para gerenciar configurações |
| `/criar_agente` | Abre wizard passo a passo para criar novo agente Telegram |
| `/criar_subagente` | Abre wizard passo a passo para criar novo sub-agente |
| `/cancelar_wizard` | Cancela wizard em andamento |

---

## Sistema de tarefas persistentes

Tarefas sobrevivem a crashes/reinicializações. Armazenadas na tabela `tasks` do SQLite.

### Ciclo de vida
```
pending → in_progress → completed
                      ↘ failed
                      ↘ paused  ← startup recovery (crash)
                      ↘ cancelled
```

### Startup recovery (post_init)
- No boot, tarefas `in_progress` → `paused`
- Usuário recebe notificação com botões **Retomar** / **Cancelar**
- Ao retomar: contexto salvo é injetado na conversa para o Claude continuar

---

## Variáveis do .env

Apenas variáveis **únicas por bot**. Variáveis globais vêm do `config.global`.

| Variável | Obrigatório | Padrão | Descrição |
|---|---|---|---|
| `TELEGRAM_TOKEN` | sim | — | Token do @BotFather |
| `BOT_NAME` | sim | nome da pasta | Nome do bot |
| `MAX_HISTORY` | — | `20` | Máx. mensagens no histórico |
| `COMPACTION_ENABLED` | — | `false` | Sumariza mensagens antigas em vez de descartar (requer OPENROUTER_API_KEY) |
| `COMPACTION_MODEL` | — | `google/gemini-2.0-flash-001` | Modelo OpenRouter para sumarização |
| `COMPACTION_KEEP` | — | `10` | Mensagens recentes preservadas ao compactar |
| `TRACING_ENABLED` | — | `true` | Habilita tracing granular por LLM call + tool call |
| `TOOLS` | — | `none` | Ferramentas ativas |
| `WORK_DIR` | — | `<bot>/workspace` | Sandbox do file tool |
| `MODEL` | — | do config.global | Override do modelo (opcional) |
| `ACCESS_MODE` | — | do config.global | Override do modo de acesso (opcional) |
| `PROVIDER` | — | do config.global | Override do provedor: `anthropic`, `openrouter`, `codex` ou `claude-cli` |
| `GROUP_MODE` | — | `always` | Comportamento em grupos: `always` (responde tudo) ou `mention_only` (só quando marcado com @bot ou reply) |
| `GUARDRAILS_ENABLED` | — | `true` | Habilita guardrails: classifica ações e notifica admin |
| `GUARDRAILS_MODE` | — | `notify` | `notify` = alerta admin, executa normal; `confirm` = **bloqueia** dangerous sem `request_approval` prévio; `block` = **sempre bloqueia** dangerous |
| `GUARDRAILS_LEVEL` | — | `dangerous` | Nível mínimo para alertas ao admin: `moderate` ou `dangerous` |
| `INJECTION_THRESHOLD` | — | `0.7` | Score mínimo para flag de injection (0.0 = desabilitado). Scoring multi-padrão: 0.3 por padrão fraco, 0.5 por padrão forte |
| `BEHAVIOR_LEARNING_ENABLED` | — | `false` | Carrega BEHAVIOR.md no system prompt e habilita extração pelo behavior-extract.sh |
| `BEHAVIOR_MAX_CHARS` | — | `2000` | Tamanho máximo do perfil comportamental no contexto |

## Variáveis do config.global

| Variável | Descrição |
|---|---|
| `ANTHROPIC_API_KEY` | Chave da API Anthropic — **opcional** se usar OAuth do Claude Code |
| `OPENROUTER_API_KEY` | Chave da API OpenRouter — necessária para bots com `PROVIDER=openrouter` |
| `OPENAI_API_KEY` | Chave da API OpenAI — **opcional** se usar OAuth do Codex CLI |
| `PROVIDER` | Provedor padrão: `anthropic`, `openrouter`, `codex` ou `claude-cli` |
| `ADMIN_ID` | Telegram ID do admin (compartilhado) |
| `MODEL` | Modelo padrão (Claude ou OpenRouter conforme PROVIDER) |
| `ACCESS_MODE` | Modo de acesso padrão (`open` / `approval` / `closed`) |
| `BUGFIXER_ENABLED` | `true` / `false` — habilita o Bug Fixer Agent (default: `false`) |
| `BUGFIXER_TIMES_PER_DAY` | Quantas vezes por dia o Bug Fixer roda via cron (default: `3`) |
| `BUGFIXER_TELEGRAM_TOKEN` | Token do bot usado para notificações ao admin. Se vazio, usa o token do primeiro bot disponível como fallback. |
| `ADMIN_PANEL_URL` | URL pública do painel admin (ex: `https://painel.seudominio.com`). Usada pelo `/painel` e `gerar-acesso.sh`. Se vazio, fallback para `http://<ip>:8080`. |

### Autenticação: API key vs Claude Code

`bot.py` escolhe automaticamente conforme `PROVIDER`:
- **anthropic** (padrão):
  1. Se `ANTHROPIC_API_KEY` está preenchida → usa API key
  2. Se não → lê `~/.claude/.credentials.json` (OAuth do Claude Code)
  O token OAuth é lido a cada chamada — renovação automática sem reiniciar.
- **openrouter**: usa `OPENROUTER_API_KEY` (obrigatória). Qualquer modelo disponível no OpenRouter.
- **codex**: modelos OpenAI via OAuth do Codex CLI (ChatGPT OAuth) ou `OPENAI_API_KEY`.
  1. Se `OPENAI_API_KEY` está preenchida → usa API key
  2. Se não → lê `~/.codex/auth.json` (OAuth do Codex CLI, campo `tokens.access_token`)
  O token OAuth é lido a cada chamada — renovação automática pelo Codex CLI sem reiniciar.
  Modelos suportados: `gpt-5.4`, `gpt-5.3-codex`, `gpt-5.2`, `gpt-5.1`, etc.
- **claude-cli**: chama `claude -p` como subprocess. Usa OAuth do Claude Code — sem API key. Conversa mantida por session_id por usuário. System prompt reinjetado a cada `/start` ou `/clear`. Ferramentas customizadas não são suportadas neste modo.

## Variáveis do secrets.env

| Variável | Ferramenta | Descrição |
|---|---|---|
| `DB_URL` | `database` | Connection string do banco |
| `GIT_TOKEN` | `git` | Token GitHub/GitLab |
| `GIT_USER` | `git` | Username git |
| `GIT_EMAIL` | `git` | Email git |
| `GITHUB_TOKEN` | `github` | Token GitHub (fallback: `GIT_TOKEN`) |
| `API_KEY_1` | livre | Chave de API extra |
| `API_KEY_2` | livre | Chave de API extra |

**Ordem de carregamento em runtime:**
1. `config.global` — defaults globais (setdefault, não sobrescreve)
2. `secrets.global` — credenciais sensíveis **compartilhadas** por todos os bots (sobrescreve)
3. `bots/<nome>/.env` — overrides por bot
4. `bots/<nome>/secrets.env` — credenciais sensíveis exclusivas do bot (sobrescreve tudo)

---

## Crons instalados

| Schedule | Script | Descrição |
|---|---|---|
| `50 23 * * *` | `memory-autosave.sh` | Destila memória diária → MEMORY.md. Usa fallback automático de provedor (Claude OAuth → Codex OAuth → OpenRouter → OpenAI). Status visível no painel admin aba Sistema. |
| `55 23 * * *` | `behavior-extract.sh` | Extrai perfil comportamental → BEHAVIOR.md (5 min após memory-autosave). Só processa bots com `BEHAVIOR_LEARNING_ENABLED=true`. |
| `0 2 * * 0` | `memory-cleanup.sh 30` | Remove diários com mais de 30 dias |
| dinâmico (`# smb-bugfixer`) | `bugfixer.py` | Bug Fixer Agent — gerado automaticamente pelo painel admin conforme `BUGFIXER_TIMES_PER_DAY` |
| `0 8 * * *` | `check-update.sh` | Verifica se origin/main tem commits novos e notifica admin |

---

## Segurança

- **Painel admin protegido por token temporário:** middleware em `admin/app.py` bloqueia todas as rotas sem cookie de sessão válido. Tokens gerados via `/painel` (Telegram) ou `gerar-acesso.sh` (CLI). In-memory, perdem-se no restart (by design). Secret HMAC vem de `ADMIN_PASSWORD` em `.env.admin`. TTL configurável via `TOKEN_TTL` no `.env.admin` (default 1800s = 30 min). Geração restrita a localhost (`/api/gen-token`).
- `.env` e `secrets.env` → `chmod 600` (só o dono lê)
- Pastas dos bots → `chmod 700`
- `bot_data.db` → `chmod 600`
- Shell tool tem denylist: bloqueia leitura de `config.global`, `.env`, `secrets.env`, `~/.claude/.credentials.json`, `~/.codex/auth.json`, `printenv`, variáveis de credencial
- `context.global` e instruções de tools não devem mandar exibir segredos; para HTTP use placeholders como `$OPENROUTER_API_KEY` para substituição em memória
- File tool é sandboxado em `WORK_DIR` (path traversal bloqueado)
- HTTP tool bloqueia IPs internos (169.254.x, localhost, metadata endpoints); resolve placeholders `$VAR`/`${VAR}` nos headers internamente — **nunca instrua o bot a imprimir secrets via shell para usá-los em chamadas HTTP**
- Git tool injeta token no URL em memória, nunca exibe nos logs
- Database tool bloqueia DROP/TRUNCATE sem WHERE
- **Regra para instruções em `soul.md` e `context.global`:** nunca oriente o modelo a usar `echo`, `printenv` ou `run_shell` para obter credenciais. Use placeholders (`$OPENROUTER_API_KEY`, `$API_KEY_1` etc.) diretamente nos campos `headers` do `http_request` — a resolução ocorre em `tools/http.py` antes do envio.

---

## Retry e resiliência

- Cliente Anthropic criado com `max_retries=3` (backoff automático do SDK para 429/500/503)
- Erros em `handle_message` notificam o admin via Telegram com detalhes do erro
- Lock per-user garante serialização de mensagens — sem perda por concorrência
- Conversas persistidas no SQLite — sobrevivem restarts
- **Lock de token**: `_check_duplicate_token()` no startup adquire lock exclusivo (`BASE_DIR/.locks/bot_<token_id>.lock`) e valida que nenhum outro bot usa o mesmo `TELEGRAM_TOKEN`. Impede 409 Conflict por instâncias simultâneas.
- **Drop pending updates**: `run_polling(drop_pending_updates=True)` descarta updates acumulados durante downtime/restart, evitando reprocessamento.
- **Markdown safety**: mensagens com `parse_mode="Markdown"` usam `escape_markdown()` em dados dinâmicos (nomes, títulos, transcrições) para evitar 400 Bad Request do Telegram.

---

## Testes

```bash
cd claude-bots
pytest tests/ -v
```

| Arquivo | Cobertura |
|---|---|
| `tests/test_security.py` | Shell denylist, path traversal, SQL safety, HTTP placeholder resolution |
| `tests/test_config.py` | Carregamento de .env, precedência de config |
| `tests/test_analytics.py` | Analytics, persistência de conversas, schedules |
