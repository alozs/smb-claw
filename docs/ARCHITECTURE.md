# Arquitetura

## Estrutura do projeto

```
smb-claw/
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
│   └── <nome>/             # Um diretório por agente
│
├── subagents/              # Subagentes especializados
│   └── <nome>/             # Um diretório por subagente
│
├── config.global           # Configurações globais
├── config.global.example   # Template de configuração
├── secrets.global          # Credenciais globais (chmod 600)
├── context.global          # Instruções de sistema globais
│
├── setup.sh                # Bootstrap: wizard CLI, deps, painel admin (--config para reconfigurar)
├── criar-bot.sh            # Cria novo agente com toda infraestrutura + systemd
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

## Sistema de memória

O system prompt é **reconstruído a cada mensagem** — mudanças nos arquivos têm efeito imediato.

| Camada | Arquivo | Descrição |
|---|---|---|
| Global | `context.global` | Instruções compartilhadas por todos os agentes |
| Personalidade | `soul.md` | Papel, especialidade e regras do agente |
| Perfil do usuário | `USER.md` | Contexto pessoal, preferências, projetos |
| Longo prazo | `MEMORY.md` | Fatos destilados (auto-atualizado toda noite) |
| Diário de hoje | `memory/YYYY-MM-DD.md` | Eventos registrados pelo agente durante o dia |
| Diário de ontem | `memory/YYYY-MM-DD.md` | Continuidade da conversa do dia anterior |

### Destilação automática

- **23:50 diariamente**: `memory-autosave.sh` lê os diários e sessões do dia, chama um LLM para extrair fatos duráveis e atualiza o `MEMORY.md`
- **02:00 aos domingos**: `memory-cleanup.sh` remove diários com mais de 30 dias

---

## Múltiplos agentes na mesma VPS

Crie quantos agentes quiser — **não existe limite no framework**. O único limite é a RAM e CPU da sua VPS.

| VPS | RAM | Agentes confortáveis |
|---|---|---|
| $5/mês | 1 GB | ~10 agentes |
| $10/mês | 2 GB | ~25 agentes |
| $20/mês | 4 GB | ~50 agentes |

Cada agente usa **long polling** do Telegram — **zero portas abertas**, sem domínio, sem SSL, sem nginx. O Telegram cuida de toda a conectividade. A única porta usada (opcionalmente) é a do painel admin (8080).

Em idle, cada agente consome **~75 MB de RAM** e **0% de CPU**. Só processa quando recebe mensagem. Sem polling pesado, sem websockets, sem overhead de container.

**Compartilhado** entre todos os agentes:
- `config.global` / `secrets.global` — chaves de API, admin ID
- `context.global` — instruções globais
- `bot.py`, `db.py`, `tools/` — código-fonte
- `subagents/` — subagentes (com controle de acesso via `ALLOWED_PARENTS`)

**Isolado** por agente:
- `.env` / `secrets.env` — token e credenciais
- `soul.md` — personalidade
- `bot_data.db` — conversas, tarefas, analytics
- `workspace/` — sandbox de arquivos
- `memory/` — memória diária e de longo prazo

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

## Gerenciamento de serviços

```bash
# Via script
bash gerenciar.sh status                 # status de todos
bash gerenciar.sh list                   # listar agentes disponíveis
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

Em VPS com systemd, cada agente roda como um serviço com `Restart=always` e `RestartSec=10` — se cair, volta sozinho. Em Docker, os agentes são iniciados via `nohup` automaticamente pelo `setup.sh`.

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

Via Telegram (em qualquer agente):
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

---

## Testes

```bash
cd smb-claw
pytest tests/ -v
```

| Arquivo | Cobertura |
|---|---|
| `tests/test_security.py` | Shell denylist, path traversal, SQL safety, HTTP placeholders |
| `tests/test_config.py` | Carregamento de .env, precedência de config |
| `tests/test_analytics.py` | Analytics, persistência de conversas, schedules |
