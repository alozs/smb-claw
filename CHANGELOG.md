# Changelog

## 0.2.0 (2026-03-15)

### Novas funcionalidades
- **Wizard de agentes via Telegram** — `/criar_agente` guia o admin passo a passo: nome, descrição, token, provedor, ferramentas e soul.md (com geração assistida por IA)
- **Wizard de sub-agentes via Telegram** — `/criar_subagente` com seleção de agente pai e fluxo idêntico ao wizard de agentes
- **Apagar agente via Telegram** — `/apagar_agente` lista agentes, pede confirmação e remove serviço + diretório completo
- **Menu de comandos nativo do Telegram** — botão `/` ao lado do campo de texto com comandos diferenciados para usuários comuns e admin (`set_my_commands` registrado no `post_init`)
- **Geração automática de soul.md** — wizard chama `ask_claude()` com prompt especializado para gerar a personalidade do agente baseada na descrição do usuário

### Melhorias de estabilidade
- **Start automático pós-criação** — `systemctl enable` + `systemctl start` executados automaticamente ao finalizar o wizard; erros de inicialização reportados com mensagem detalhada
- **Verificação de retorno do systemctl** — wizard reporta erro específico se o serviço falhar ao iniciar (ex: token inválido)
- **Escape de Markdown no Telegram** — corrigido erro `BadRequest: Can't parse entities` causado por underscores não escapados em textos enviados com `parse_mode=Markdown`
- **Wizard resiliente a cancelamento** — `/cancelar_wizard` encerra o fluxo em qualquer etapa sem deixar estado residual

### Melhorias de segurança (painel admin)
- **Variáveis de sistema protegidas** — `PROVIDER`, `ADMIN_ID`, `MODEL`, `ACCESS_MODE`, `BUGFIXER_*`, `OPENROUTER_API_KEY` ficam em modo readonly no painel: sem edição, sem exclusão, sem salvar
- **Crons do sistema protegidos** — entradas com tag `# [system]` ou `# smb-*` no crontab não podem ser removidas pelo painel (validação no backend com HTTP 422)
- **Endpoint dedicado de system-keys** — `GET /api/global/system-keys` retorna quais chaves são protegidas por arquivo; registrado antes da rota parametrizada para evitar conflito de path
- **context.global com fluxo Edit/Save/Restore** — textarea desabilitada por padrão; requer clique em Editar para habilitar; botão Restaurar Padrão reverte para `context.global.default`

### UX / painel admin
- **Linguagem unificada** — todas as referências a "bot/bots" nas telas de configurações alteradas para "agente/agentes"
- **Ícone no header de configurações** — ícone de settings alinhado com o título da página
- **Modal Novo Agente redesenhado** — layout 2 colunas, ícone no header, sem barra de rolagem
- **Descrição do Memory Autosave corrigida** — texto e ícone atualizados para seguir o padrão visual do painel
- **Provedor do memory-autosave** — ordem de fallback alterada para: Claude OAuth → Codex OAuth → OpenRouter → OpenAI API Key

## 0.1.5 (2026-03-15)
- remove card de suporte a mídia da seção de recursos
- design: melhora seção origem, bento grid recursos, favicon e ajustes tabela
- fix: corrige descrição de suporte a mídia na landing page
- design: redesign completo da landing page
- docs: adiciona landing page GitHub Pages e história de origem do projeto


## 0.1.5 (2026-03-15)
- remove card de suporte a mídia da seção de recursos
- design: melhora seção origem, bento grid recursos, favicon e ajustes tabela
- fix: corrige descrição de suporte a mídia na landing page
- design: redesign completo da landing page
- docs: adiciona landing page GitHub Pages e história de origem do projeto

## 0.1.4 (2026-03-15)
- fix: remove todos os paths hardcoded /home/ubuntu — agora usa paths dinâmicos

## 0.1.3 (2026-03-14)
- docs: adiciona versionamento, contagem de linhas e comandos /version /update ao README

## 0.1.2 (2026-03-14)
- fix: gitignore para locks, bugfixer_state e avatars + release.sh não usa git add -A

## 0.1.1 (2026-03-14)
- feat: sistema de versionamento com release/update via Telegram
- feat: timezone UTC-3, edição de agendamentos e suporte a day_of_month
- chore: update OpenAI models to GPT-5.x codex family
- docs: expand providers section with OAuth setup guides and visual formatting
- feat: telegram markdown safety, token lock protection, admin panel v3 and comprehensive README
- refactor: improve subagent tool isolation, analytics tracking and user_id propagation
- feat: subagents, bug fixer agent, admin panel v2 and media support
- security: remove secret-printing guidance and add HTTP placeholder resolution
- Merge pull request #1 from alozs/copilot/check-sensitive-data-exposure
- Generalize safe HTTP secret placeholders
- Prevent secret exposure in OpenRouter guidance
- Initial plan
- chore: prepare repo for public release
- feat: initial commit — SMB Claw platform

## 0.1.0 (2026-03-15)
- Initial release
