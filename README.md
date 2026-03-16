# SMB Claw — Multi-Agent AI Framework

Sistema de gestão de agentes de IA para Telegram, focado em **simplicidade**, **facilidade de uso** e **segurança**. Rode múltiplos agentes independentes numa única VPS, cada um com personalidade própria, memória persistente, ferramentas modulares e subagentes especializados.

Pensado para quem quer colocar agentes de IA em produção rápido — sem infraestrutura complexa, sem Kubernetes. Um script e você tem um agente funcionando. Roda em VPS com systemd ou em Docker.

## Por que o SMB Claw existe?

Depois de semanas passando raiva com o OpenClaw — que às vezes funciona, às vezes não, você pede uma coisa e ele faz outra — decidi criar minha própria alternativa com uma proposta diferente: **extremamente simples, eficiente e funcionando como um verdadeiro assistente de IA confiável**.

Sem precisar lidar com infraestrutura complexa como Kubernetes. Basicamente um script e você já tem um agente rodando — do jeito que deveria ser desde o começo. Funciona tanto em VPS com systemd quanto em containers Docker.

### SMB Claw vs OpenClaw

|  | **SMB Claw** | **OpenClaw** |
|---|---|---|
| **RAM por agente** | ~75 MB idle | 8–16 GB recomendado |
| **Setup** | 1 script (`bash setup.sh`) | Docker Compose ou Kubernetes |
| **Tempo até primeiro agente** | ~2 minutos | 15–60 min (config, permissões, sandbox) |
| **Codebase** | ~11.300 linhas | ~350.000+ linhas |
| **Infraestrutura** | VPS $5/mês ou Docker | VPS $20+/mês ou cluster K8s |
| **50 agentes simultâneos** | VPS 4 GB (~$20/mês) | Cluster com 32+ GB RAM |
| **Segurança** | Sandbox por padrão, shell denylist, path traversal bloqueado | Vulnerabilidades documentadas pela Cisco e Kaspersky (porta aberta, plugins maliciosos) |
| **Memória entre restarts** | SQLite persistente — sobrevive crashes | Volátil — perde contexto ao reiniciar |
| **Confiabilidade** | Faz o que você pede | Loops de raciocínio desnecessários, reinterpreta objetivos |
| **Provedores** | Claude, OpenAI, OpenRouter (4 modos) | 15+ provedores |
| **Integrações** | Telegram (foco total) | 50+ plataformas |
| **Painel admin** | FastAPI com token temporário (HMAC) | Dashboard na porta 18789 (exposta por padrão) |
| **Criador** | Mantido ativamente | Criador saiu para a OpenAI (2026) |

> **Resumo:** O OpenClaw é poderoso e extensível, mas exige infraestrutura robusta, configuração extensa e tem problemas documentados de segurança e estabilidade. O SMB Claw troca extensibilidade por **simplicidade radical** — setup em 2 minutos, 100x menos RAM, zero configuração de rede, e faz exatamente o que você pede.

### Ultra leve

Todo o sistema — agent engine, painel admin, ferramentas, scheduler, bugfixer — soma **~11.300 linhas de código** (Python + HTML + Shell). Sem frameworks pesados, sem camadas de abstração desnecessárias.

Cada agente consome **~75 MB de RAM** em idle e **zero portas de rede** — usa long polling do Telegram, não precisa abrir porta nem configurar domínio/SSL. A única porta usada é a do painel admin (8080), e ela é opcional.

Rode **10, 20, 50 agentes** na mesma VPS de $5/mês. Eles só processam quando recebem mensagem — sem polling pesado, sem websockets, sem overhead. Uma VPS com 2 GB de RAM já aguenta dezenas de agentes simultâneos sem suar.

## Destaques

- **Ultra leve** — ~75 MB por agente em idle, zero portas abertas, escala em VPS barata
- **Sem limites de agentes** — crie quantos quiser, cada um isolado com seu próprio banco, memória e personalidade
- **Multi-provedor** — Claude (API ou CLI), OpenRouter (Grok, GPT-4o, Gemini), OpenAI (API ou ChatGPT Plus/Pro)
- **Memória em camadas** — personalidade, perfil do usuário, memória de longo prazo, diários diários
- **Ferramentas modulares** — shell, HTTP, Git, GitHub, banco de dados, arquivos, cron
- **Subagentes** — delegue tarefas para agentes especializados (geração de imagens, análises etc.)
- **Wizard CLI** — setup interativo no terminal com OAuth integrado (funciona em Docker e SSH headless)
- **Reconfiguração fácil** — `./setup.sh --config` para editar valores sem reinstalar
- **Painel web** — dashboard FastAPI com setup wizard, editor de configuração e logs em tempo real
- **Wizard via Telegram** — crie e apague agentes e sub-agentes direto pelo chat, passo a passo, com geração de personalidade assistida por IA
- **Docker ready** — detecta containers automaticamente, ajusta inicialização e orienta exposição de portas
- **Menu de comandos** — botão nativo do Telegram com todos os comandos disponíveis (diferente por usuário comum vs admin)
- **Bug Fixer autônomo** — agente que monitora erros e tenta corrigir sozinho
- **Segurança por padrão** — tokens isolados, sandbox de comandos, controle de acesso por aprovação

---

## Requisitos

- **Python 3.10+**
- **Linux** (Ubuntu/Debian recomendado) — com systemd ou Docker
- **Token do Telegram** via [@BotFather](https://t.me/BotFather) (um por agente)
- **Provedor de IA** (pelo menos um):
  - [Claude Code](https://claude.ai) — assinatura ativa (sem API key, usa OAuth)
  - [Anthropic API](https://console.anthropic.com) — API key
  - [OpenRouter](https://openrouter.ai) — API key (acesso a Grok, GPT-4o, Gemini, Mistral etc.)
  - [OpenAI/Codex](https://platform.openai.com) — API key ou ChatGPT Plus/Pro OAuth (sem custo extra)

### Dependências opcionais

| Dependência | Para quê |
|---|---|
| `ffmpeg` + `openai-whisper` | Transcrição de áudio e vídeo |
| `pdfplumber` | Extração de texto de PDFs |
| `Node.js` | Claude Code CLI e Codex CLI |

---

## Instalação

```bash
git clone https://github.com/alozs/smb-claw.git
cd smb-claw
bash setup.sh
```

O `setup.sh` faz tudo automaticamente:
1. Instala dependências do sistema (python3, pip, git, lsof) se necessário
2. Verifica Python, Node, ffmpeg, Claude CLI, Codex CLI
3. Detecta autenticações já configuradas (OAuth, API keys)
4. Instala pacotes Python faltantes
5. Inicia o painel admin
6. Abre o **wizard CLI interativo** na primeira execução

### Wizard CLI (terminal)

Na primeira execução, o setup roda um wizard interativo direto no terminal:
- Escolhe o provedor de IA (Anthropic, OpenRouter, OpenAI/Codex)
- Configura autenticação (OAuth do Claude/Codex ou API key)
- Define modelo, Admin ID e modo de acesso
- Cria e inicia o primeiro agente automaticamente

Funciona em qualquer ambiente — VPS, Docker, SSH headless.

### Reconfigurar (`--config`)

Para editar a configuração existente sem reinstalar tudo:

```bash
./setup.sh --config    # ou -c
```

Mostra os valores atuais entre parênteses — pressione Enter para manter ou digite um novo valor. Útil para trocar de provedor, modelo ou modo de acesso.

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

### Via Telegram (wizard — recomendado)

Envie `/criar_agente` para qualquer agente como admin. O wizard guia passo a passo:

1. **Nome** — slug do agente (ex: `assistente-vendas`)
2. **Descrição** — o que ele faz em uma frase
3. **Token** — cole o token do @BotFather
4. **Provedor de IA** — escolha via botões (Claude OAuth, Anthropic API, OpenRouter, Codex)
5. **Ferramentas** — selecione com toggle (terminal, arquivos, HTTP, Git, GitHub, banco de dados)
6. **Personalidade** — gere automaticamente com Claude ou cole o texto manualmente
7. **Confirmação** — resumo completo antes de criar

Ao confirmar, o agente é criado, o serviço systemd é habilitado e iniciado automaticamente.

Para remover um agente: `/apagar_agente` → selecione → confirme. Para e deleta tudo (serviço, diretório, banco).

### Via painel web

Acesse o dashboard → botão **"Novo Agente"** → preencha nome, token e personalidade.

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

## Provedores de IA

O framework suporta **4 provedores** com autenticação flexível — API key ou OAuth automático.

### `claude-cli` — Recomendado

> Usa a assinatura do Claude Code. **Sem custo extra de API**, sem API key. Token OAuth renovado automaticamente.

```bash
# 1. Instale o Claude Code CLI
npm install -g @anthropic-ai/claude-code

# 2. Faça login (abre navegador para autenticar)
claude login

# 3. Configure o provedor (config.global)
PROVIDER=claude-cli
MODEL=claude-sonnet-4-6
```

### `anthropic` — API direta

> API key da Anthropic ou OAuth do Claude Code.

**API key:** coloque `ANTHROPIC_API_KEY=sk-ant-api03-...` no `secrets.global`
**OAuth:** basta ter feito `claude login` — o framework detecta automaticamente

### `codex` — OpenAI / ChatGPT

> Modelos OpenAI via API key ou OAuth do ChatGPT (plano Plus/Pro).

**API key:** coloque `OPENAI_API_KEY=sk-...` no `secrets.global`
**OAuth (ChatGPT Plus/Pro — sem custo extra):**
```bash
npm install -g @openai/codex
codex login
# config.global: PROVIDER=codex  MODEL=gpt-5.4
```

> Com OAuth, usa a Responses API via endpoint WHAM. Com API key, usa Chat Completions. A detecção é automática.

### `openrouter` — Multi-modelo

> Acesso a dezenas de modelos via uma única API key (Grok, GPT-4o, Gemini, Mistral, LLaMA).

Coloque `OPENROUTER_API_KEY=sk-or-v1-...` no `secrets.global`.

### Resumo de autenticação

| Provedor | API key | OAuth CLI | Custo |
|:---|:---:|:---:|:---|
| `claude-cli` | — | `claude login` | Incluso na assinatura Claude Code |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude login` | Pay-per-use (Anthropic) |
| `codex` | `OPENAI_API_KEY` | `codex login` | API key: pay-per-use / OAuth: incluso no ChatGPT Plus/Pro |
| `openrouter` | `OPENROUTER_API_KEY` | — | Pay-per-use (OpenRouter) |

> **Dica:** OAuth é sempre prioridade menor que API key. Se `ANTHROPIC_API_KEY` estiver definida, ela é usada mesmo com OAuth disponível. Remova a key para forçar OAuth.

---

## Comandos Telegram

### Todos os usuários

| Comando | Descrição |
|---|---|
| `/start` | Inicia sessão, limpa histórico |
| `/clear` | Limpa histórico de conversa |
| `/info` | Exibe o soul.md do agente |
| `/id` | Retorna o Telegram ID do usuário |
| `/tasks [status]` | Lista tarefas |

### Somente admin

| Comando | Descrição |
|---|---|
| `/users` | Lista usuários aprovados |
| `/pending` | Lista solicitações pendentes |
| `/revoke <id>` | Revoga acesso de um usuário |
| `/memory` | Mostra status dos arquivos de memória |
| `/stats [período]` | Analytics: tokens, custo, mensagens |
| `/restart` | Reinicia o agente |
| `/version` | Versão atual e atualizações pendentes |
| `/update` | Puxa atualizações e reinicia serviços |
| `/criar_agente` | Wizard para criar novo agente |
| `/criar_subagente` | Wizard para criar novo sub-agente |
| `/apagar_agente` | Remove um agente |
| `/painel [min]` | Link temporário do painel admin |
| `/cancelar_wizard` | Cancela wizard em andamento |

### Modos de acesso

| Modo | Comportamento |
|---|---|
| `open` | Qualquer pessoa pode usar |
| `approval` | Admin aprova novos usuários via botões inline (padrão) |
| `closed` | Apenas usuários pré-aprovados |

---

## Documentação detalhada

| Documento | Conteúdo |
|---|---|
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Variáveis de ambiente, ferramentas, subagentes, mídia, modelos |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Estrutura do projeto, memória, concorrência, serviços, versionamento |
| [docs/ADMIN-PANEL.md](docs/ADMIN-PANEL.md) | Painel web, API REST, funcionalidades |
| [docs/SECURITY.md](docs/SECURITY.md) | Proteções, autenticação do painel, HTTPS, firewall |

---

## Licença

MIT
