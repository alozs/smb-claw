# Claude Multi-Bot Framework

Framework para rodar múltiplos bots de IA no Telegram, cada um com personalidade própria, memória persistente e ferramentas modulares. Suporta Claude (Anthropic), OpenRouter (Grok, GPT-4o, Gemini) e qualquer modelo compatível.

## Visão Geral

```
claude-bots/
├── bot.py              # Core: handlers, loop principal, integração Telegram
├── db.py               # Persistência SQLite (WAL mode)
├── scheduler.py        # Notificações proativas agendadas
├── security.py         # Sandbox de shell, proteção path traversal
├── bugfixer.py         # Agente autônomo de correção de erros
├── config.global       # Configurações globais (baseie-se no config.global.example)
├── secrets.global      # Credenciais globais (NUNCA commitar)
├── context.global      # Instruções de sistema globais para todos os bots
├── tools/              # Ferramentas modulares
│   ├── shell.py        # Execução de comandos shell
│   ├── http.py         # Requisições HTTP
│   ├── git.py          # Operações Git
│   ├── github_tool.py  # API GitHub
│   ├── database.py     # Queries SQL (PostgreSQL, MySQL, SQLite)
│   ├── memory.py       # Sistema de memória em camadas
│   ├── schedule.py     # Agendamentos via SQLite
│   ├── tasks.py        # Gerenciamento de tarefas
│   ├── agent.py        # Subagentes Claude
│   └── telegram_file.py# Envio de arquivos via Telegram
├── admin/              # Painel web de administração (FastAPI)
├── bots/               # Instâncias dos bots (não versionado)
├── subagents/          # Subagentes especializados
├── criar-bot.sh        # Cria um novo bot com toda a infraestrutura
├── gerenciar.sh        # Gerencia serviços (start/stop/restart/logs)
├── configurar-secrets.sh # Configura credenciais de forma segura
└── load-envs.sh        # Carrega variáveis de ambiente globais
```

## Pré-requisitos

- Python 3.10+
- systemd (Linux)
- Conta no Telegram + token via [@BotFather](https://t.me/BotFather)
- Uma das opções de IA:
  - **Claude Code** (assinatura ativa, sem API key necessária)
  - **Anthropic API key** (console.anthropic.com)
  - **OpenRouter API key** (openrouter.ai, para Grok, GPT-4o, Gemini etc.)

## Instalação

```bash
git clone https://github.com/seu-usuario/claude-bots.git
cd claude-bots
pip install -r requirements.txt
```

### Configuração global

```bash
cp config.global.example config.global
nano config.global   # preencha ADMIN_ID e defina o PROVIDER
```

Para credenciais globais (opcional):

```bash
cp secrets.global.example secrets.global   # se existir
chmod 600 secrets.global
nano secrets.global  # ANTHROPIC_API_KEY, OPENROUTER_API_KEY etc.
```

## Criando um Bot

```bash
bash criar-bot.sh meu-assistente
```

O script cria a estrutura completa em `bots/meu-assistente/` e registra o serviço systemd.

### Próximos passos após criar

1. **Obtenha o token** no [@BotFather](https://t.me/BotFather):
   ```bash
   nano bots/meu-assistente/.env
   # → TELEGRAM_TOKEN=<token>
   ```

2. **Defina a personalidade** do bot:
   ```bash
   nano bots/meu-assistente/soul.md
   ```

3. **(Opcional)** Configure credenciais sensíveis:
   ```bash
   bash configurar-secrets.sh meu-assistente
   ```

4. **Inicie o serviço**:
   ```bash
   sudo systemctl start claude-bot-meu-assistente
   sudo systemctl enable claude-bot-meu-assistente
   ```

5. **Acompanhe os logs**:
   ```bash
   sudo journalctl -u claude-bot-meu-assistente -f
   ```

## Estrutura de um Bot

```
bots/meu-assistente/
├── .env          # Token do Telegram, modelo, ferramentas, provedor
├── secrets.env   # DB, Git, APIs (chmod 600, não versionado)
├── soul.md       # Personalidade e instruções do bot
├── USER.md       # Perfil do usuário (contexto pessoal)
├── MEMORY.md     # Memória de longo prazo (auto-preenchida)
├── memory/       # Diários diários (auto-gerados)
└── workspace/    # Arquivos do bot
```

### Exemplo de `.env` de um bot

```env
TELEGRAM_TOKEN=seu_token_aqui
BOT_NAME=Assistente
MAX_HISTORY=20

# Ferramentas disponíveis: none | shell,cron,files,http,git,github,database
TOOLS=shell,http,database

# Provedor de IA
PROVIDER=claude-cli   # claude-cli | anthropic | openrouter

# Modelo (herda do config.global se não definido)
# MODEL=claude-opus-4-6

# Modo de acesso: open | approval | closed
ACCESS_MODE=approval
```

## Provedores de IA

| Provider | Configuração | Modelos |
|---|---|---|
| `claude-cli` | Assinatura Claude Code (sem API key) | Claude Opus, Sonnet, Haiku |
| `anthropic` | `ANTHROPIC_API_KEY` no `secrets.global` | Claude Opus, Sonnet, Haiku |
| `openrouter` | `OPENROUTER_API_KEY` no `secrets.global` | Grok 3, GPT-4o, Gemini Flash, Mistral... |

### Exemplos de modelos OpenRouter

```env
MODEL=x-ai/grok-3              # Grok 3 (xAI)
MODEL=x-ai/grok-3:online       # Grok 3 com busca em tempo real
MODEL=google/gemini-2.0-flash  # Gemini Flash
MODEL=openai/gpt-4o            # GPT-4o
MODEL=mistralai/mistral-small-3.1
```

## Ferramentas Modulares

Habilite por bot via `TOOLS=` no `.env`:

| Ferramenta | Funcionalidade |
|---|---|
| `shell` | Executa comandos no servidor |
| `http` | Faz requisições HTTP/REST |
| `git` | Clone, commit, push, pull |
| `github` | API GitHub (issues, PRs, repos) |
| `database` | Queries SQL (PostgreSQL, MySQL, SQLite) |
| `files` | Envia arquivos via Telegram |
| `cron` | Gerencia crontabs do sistema |

## Subagentes

Subagentes são bots especializados que podem ser invocados pelo bot pai para tarefas específicas. Ficam em `subagents/<nome>/`.

```
subagents/criador-graficos/
├── .env        # NAME, DESCRIPTION, PROVIDER, MODEL, TOOLS, ALLOWED_PARENTS, MODE
└── soul.md     # System prompt especializado
```

### Configuração do `.env` do subagente

```env
NAME=Criador de Gráficos
DESCRIPTION=Gera gráficos e visualizações de dados
PROVIDER=anthropic
MODEL=claude-sonnet-4-20250514
TOOLS=shell,files          # apenas as ferramentas que o subagente precisa
ALLOWED_PARENTS=*          # quais bots podem invocar (* = todos)
MODE=simple                # simple (uma chamada, sem tools) | agentic (loop com tools)
```

- **Modo `simple`**: uma única chamada LLM, sem ferramentas. Rápido, ideal para 95% dos casos.
- **Modo `agentic`**: loop com tool use (até 10 iterações). Para tarefas complexas.
- Subagentes recebem **apenas** as ferramentas declaradas no seu `TOOLS` — ferramentas "sempre ativas" do bot pai (tasks, memory, schedule) não são herdadas.
- Subagentes **não podem invocar outros subagentes** (anti-recursão).
- Analytics de subagentes são registrados separadamente com identificador `<bot>/sub:<agent_name>`.

## Sistema de Memória

Cada bot possui memória em camadas, carregada automaticamente no contexto:

1. **soul.md** — personalidade e regras permanentes
2. **USER.md** — perfil do usuário
3. **MEMORY.md** — memória de longo prazo (editável pelo bot)
4. **memory/YYYY-MM-DD.md** — diário diário (auto-gerado)
5. **context.global** — instruções compartilhadas por todos os bots

## Painel Admin

Interface web para gerenciar bots, editar configurações e visualizar logs:

```bash
cd admin
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Acesse em `http://localhost:8000`.

## Múltiplos Bots na Mesma VPS

O framework suporta quantos bots você quiser na mesma VPS, sem conflito de portas. Cada bot usa **polling** (long polling do Telegram) — nenhuma porta é aberta por bot.

O que é **compartilhado** entre todos os bots:
- `config.global` / `secrets.global` — chaves de API, admin ID
- `context.global` — instruções globais injetadas no system prompt
- `bot.py`, `db.py`, `tools/` — código-fonte

O que cada bot tem **isolado**:
- `.env` — token Telegram, ferramentas, modelo
- `soul.md` — personalidade
- `bot_data.db` — conversas, tarefas, analytics
- `workspace/` — sandbox de arquivos
- `memory/` — memória diária e de longo prazo

A única porta utilizada é a do **painel admin** (FastAPI na porta 8000), que é um só para todos os bots.

Não há limite prático além dos recursos da VPS (CPU, RAM, API rate limits). Cada bot consome pouca memória em idle — só processa quando recebe mensagem.

## Gerenciamento de Serviços

```bash
# Status de todos os bots
bash gerenciar.sh status

# Listar bots disponíveis
bash gerenciar.sh list

# Iniciar / parar / reiniciar (um ou todos)
bash gerenciar.sh start meu-assistente
bash gerenciar.sh stop meu-assistente
bash gerenciar.sh restart meu-assistente
bash gerenciar.sh start   # inicia todos
bash gerenciar.sh stop    # para todos

# Logs em tempo real
bash gerenciar.sh logs meu-assistente

# Via systemctl diretamente
sudo journalctl -u claude-bot-meu-assistente -f
```

## Bug Fixer Agent

O `bugfixer.py` é um agente autônomo que monitora os logs dos bots, detecta erros e tenta corrigi-los automaticamente. Configure no `config.global`:

```env
BUGFIXER_ENABLED=true
BUGFIXER_TIMES_PER_DAY=1
BUGFIXER_TELEGRAM_TOKEN=token_de_qualquer_bot  # para notificações
```

## Segurança

- **Tokens e credenciais** ficam em arquivos `chmod 600` fora do controle de versão
- **Modo `approval`** (padrão): o admin aprova novos usuários via Telegram
- **Modo `open`**: qualquer usuário pode interagir
- **Modo `closed`**: apenas usuários aprovados previamente
- O `security.py` faz sandbox de comandos shell e proteção contra path traversal

## Testes

```bash
pytest tests/
```

## Licença

MIT
