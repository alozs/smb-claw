# Changelog

## 0.13.2 (2026-03-29)

Melhorias

• Removidos os comandos /tasks, /memory, /stats e /trace do Telegram, simplificando a interface do bot e reduzindo comandos que expunham detalhes internos do sistema ao usuário final

## 0.13.1 (2026-03-29)



### Correções

• Corrigida falha silenciosa na notificação Telegram ao realizar um release — o admin agora recebe a mensagem de confirmação corretamente após cada atualização de versão

## 0.13.0 (2026-03-28)



## Novas funcionalidades

• Suporte a WhatsApp como canal de comunicação — agora cada bot pode operar via Telegram ou WhatsApp, configurável pela variável CHANNEL no .env do bot
• Conexão WhatsApp via QR code com gerenciamento de sessão integrado ao painel admin (status de conexão, reconexão e logout)
• Criação de bots WhatsApp pelo terminal com ./criar-bot.sh meu-bot --channel whatsapp
• O wizard /criar_agente no Telegram agora permite escolher o canal (Telegram ou WhatsApp) durante a criação do bot

## 0.12.0 (2026-03-24)

**Correções**

• O wizard de criação de agentes agora exibe corretamente todas as etapas em telas menores, com scroll vertical funcional e sem corte de conteúdo nos passos mais longos.
• O painel admin passou a refletir com precisão as ferramentas realmente configuradas por bot, removendo a exibição incorreta de ferramentas sempre ativas que não faziam parte da configuração individual.

## 0.11.1 (2026-03-24)

• Melhorias internas e correções de estabilidade.

## 0.11.0 (2026-03-24)

• Melhorias internas e correções de estabilidade.

## 0.10.0 (2026-03-23)

Novas funcionalidades

• Integração com Notion: bots podem agora buscar, ler e criar páginas e databases diretamente no Notion. Basta configurar a chave `NOTION_API_KEY` no `secrets.env` e adicionar `notion` à variável `TOOLS` do bot.

Melhorias

• Painel admin com informações mais precisas sobre os recursos disponíveis, evitando confusão sobre funcionalidades ainda não implementadas.

Correções

• Arquivo de tracing (`tracer.py`) que estava ausente do repositório foi restaurado, garantindo o funcionamento correto do rastreamento granular de chamadas LLM e tool calls.
• Removida indicação incorreta de que o `action_log` poderia ser visualizado pelo admin no painel — o recurso ainda não está implementado e a interface agora reflete isso com precisão.

## 0.9.0 (2026-03-21)

Novas funcionalidades

• Guardrails leves com três modos operacionais: notify (alerta o admin sem bloquear), confirm (exige aprovação via request_approval antes de ações perigosas) e block (sempre bloqueia ações classificadas como perigosas).
• Classificação automática de ações em três níveis — seguro, moderado e perigoso — cobrindo todas as ferramentas do framework.
• Tool request_approval: o LLM pode solicitar confirmação do usuário antes de executar ações sensíveis, com fluxo integrado ao chat.
• Detecção de prompt injection por scoring: 25+ padrões em português e inglês, threshold configurável. Injeta aviso automático no system prompt quando score ultrapassa o limite.
• Aprendizado comportamental opt-in: arquivo BEHAVIOR.md gerado e atualizado toda noite por LLM, carregado no contexto para o agente antecipar necessidades do usuário.
• Auditoria completa de ações: tabela action_log registra todas as chamadas de ferramentas com classificação e score, limpeza automática após 30 dias.
• migrate-env.sh: novas variáveis de configuração são adicionadas automaticamente aos bots existentes após cada atualização via update.sh.

Melhorias

• 185 testes automatizados cobrindo guardrails, detecção de injection, configuração e analytics.

## 0.8.0 (2026-03-20)

Novas funcionalidades

• Debounce de mensagens: mensagens enviadas em rápida sucessão são consolidadas em uma única requisição ao LLM, reduzindo chamadas desnecessárias à API. Intervalo configurável via variável DEBOUNCE_SECONDS.

## 0.7.6 (2026-03-20)

Correções

• Melhorias no processo de release: script release.sh mais robusto, com geração de changelog mais precisa.

## 0.7.5 (2026-03-20)

Correções

• Correções no script release.sh para inclusão correta de todos os arquivos no commit de release.

## 0.7.4 (2026-03-20)

Melhorias

• Correções e melhorias internas no painel admin.

## 0.7.3 (2026-03-20)

Melhorias

• Comando /tasks aprimorado: agendamentos ativos agora aparecem na listagem com horário convertido para BRT e dias da semana em português.
• Suporte a grupos melhorado: comportamento configurável via GROUP_MODE (always ou mention_only).

## 0.7.2 (2026-03-20)

Novas funcionalidades

• O menu de administração agora conta com um botão dedicado para atualizar o sistema diretamente pelo Telegram, sem necessidade de acesso ao servidor via terminal.

Melhorias

• O botão de reset de conversa foi renomeado de "Limpar histórico" para "Nova conversa", tornando sua função mais intuitiva para o usuário final.

## 0.7.1 (2026-03-20)

Melhorias

• Landing page completamente redesenhada com foco em clareza e proposta de valor — agora mais fácil de entender o que o SMB Claw oferece em segundos
• Hierarquia visual e tipografia aprimoradas para uma experiência mais profissional, no estilo de ferramentas developer de alto padrão
• Ícones Lucide e logo integrados à hero section para melhor identidade visual
• Seção de sub-agentes simplificada, com linguagem mais direta e menos jargão
• Depoimento revisado para soar mais natural e autêntico
• Tabela de recursos enxugada e mais fácil de escanear

Correções

• Removido item pessoal que havia entrado indevidamente no changelog da v0.7.0
• Script `release.sh` corrigido para incluir todos os arquivos modificados no commit de release, evitando que arquivos fiquem de fora
• Arquivos `bot.py`, `CLAUDE.md` e `memory-autosave.sh` que ficaram ausentes do release v0.7.0 foram adicionados corretamente ao repositório
• Arquivo `radar-collect.py` adicionado ao `.gitignore` para não ser rastreado acidentalmente

## 0.7.0 (2026-03-17)

**Novas funcionalidades**

• Raciocínio estendido por sessão: novo comando `/thinking` permite ativar o modo de pensamento profundo do modelo em quatro níveis (off, low, medium, high), com orçamentos de até 16 mil tokens de raciocínio interno. Funciona nos provedores Anthropic e Claude-CLI.

**Melhorias**

• Outputs de ferramentas muito longos são truncados automaticamente em 12 mil caracteres em todos os provedores (Anthropic, OpenRouter, Codex), evitando estouro de contexto e erros silenciosos.

• Mensagens que ultrapassam o limite `MAX_HISTORY` agora são arquivadas no banco de dados em vez de simplesmente descartadas, preservando o histórico completo para consulta futura.

• O destilador de memória diária (`memory-autosave.sh`) passa a alertar o modelo para compactar o `MEMORY.md` quando o arquivo ultrapassa 6 KB, evitando crescimento indefinido da memória de longo prazo.

## 0.6.16 (2026-03-17)

• `git_op` agora resolve `token_var` diretamente do ambiente do processo (secrets.env), não apenas das variáveis fixas do TOOL_CONFIG — corrige erro "terminal prompts disabled" ao clonar repos privados com tokens customizados

## 0.6.15 (2026-03-17)

**Correções**

• A notificação de ferramenta ativa (indicador que aparece enquanto o agente executa uma ação) agora funciona corretamente em todos os provedores suportados — Anthropic, OpenRouter, Codex e claude-cli. Antes, a exibição era inconsistente dependendo do provedor configurado.

## 0.6.14 (2026-03-17)

• Mensagem "⏳ Pensando..." com animação agora é exibida para todos os provedores (Codex, Anthropic, OpenRouter), não apenas para claude-cli

## 0.6.13 (2026-03-17)

Correções

• Erros ocorridos durante a execução de ferramentas agora são sempre reportados ao usuário e ao admin, eliminando casos em que falhas passavam despercebidas sem nenhuma mensagem de aviso.

## 0.6.12 (2026-03-17)

• Dockerfile completo: adiciona git, curl, lsof, cron e ffmpeg às dependências de sistema
• requirements.txt: inclui fastapi, uvicorn, jinja2, python-multipart e aiofiles (admin panel)
• entrypoint.sh: inicializa cron daemon, limpa locks órfãos, sobe admin panel e todos os bots; watchdog interno reinicia automaticamente processos que caírem
• docker-compose.yml: usa entrypoint.sh como command, garantindo inicialização correta de todos os serviços

## 0.6.11 (2026-03-17)

Correções

• Ambientes Docker agora funcionam corretamente com agendamentos: o pacote `cron` é instalado automaticamente durante o setup, eliminando falhas silenciosas em containers que não o incluem por padrão.
• Quando o `cron` não está disponível no sistema, o bot exibe uma mensagem de erro clara e direta em vez de falhar sem explicação, facilitando o diagnóstico em ambientes restritos.

## 0.6.10 (2026-03-17)

• Corrige conflito de lock ao reiniciar bot pelo painel admin: espera o processo morrer e limpa o lock antes de iniciar a nova instância
• Remove referência a nome de token pessoal nos exemplos do context.global e git_op

## 0.6.9 (2026-03-17)

**Novas funcionalidades**

• A ferramenta Git agora suporta operações avançadas de sincronização com repositórios remotos: é possível fazer fetch para atualizar referências sem mesclar, checkout para trocar de branch e pull direcionado a uma branch específica — tudo diretamente pelo agente, sem precisar de comandos shell manuais.

• A autenticação Git ganhou flexibilidade: agora é possível indicar qual variável de ambiente contém o token de acesso, permitindo que diferentes bots ou sub-agentes usem credenciais distintas sem conflito.

## 0.6.8 (2026-03-17)



### Correções

• Reforço na segurança do shell: comandos potencialmente destrutivos como `kill`, `pkill`, `systemctl` e acesso direto ao `bot.py` agora são bloqueados pela denylist do shell tool, impedindo que agentes interrompam serviços ou processos do sistema de forma acidental ou maliciosa.

## 0.6.7 (2026-03-17)

Correções

• Processo de atualização via /update agora aguarda corretamente o encerramento de cada bot antes de reiniciá-lo, eliminando conflitos de instâncias duplicadas em ambientes Docker
• Locks obsoletos são automaticamente removidos durante o restart, prevenindo falhas de inicialização causadas por arquivos de lock órfãos após paradas inesperadas

## 0.6.5 (2026-03-17)



### Novas funcionalidades

• Refresh automático do token OAuth do Codex: quando o token de acesso expira e a API retorna erro 403, o sistema agora renova a credencial automaticamente sem necessidade de intervenção manual ou reinício do bot. Bots configurados com provider `codex` mantêm operação contínua mesmo após expiração do token.

## 0.6.4 (2026-03-17)



### Correções

• Corrigido problema onde comandos de gerenciamento de processos podiam interpretar incorretamente nomes de diretórios contendo hífens duplos, evitando falhas ao iniciar ou parar bots com caminhos como `--bot-dir`.

## 0.6.3 (2026-03-17)



### Correções

• Corrigido o endpoint de autenticação OAuth do provedor Codex, garantindo que bots configurados com modelos OpenAI voltem a funcionar normalmente

### Melhorias

• O painel admin agora exibe o provedor efetivo de cada bot (API key, OAuth Claude, OAuth Codex, OpenRouter), facilitando o diagnóstico de problemas de autenticação
• Aprimorado o sistema de notificações proativas, tornando os agendamentos mais confiáveis e consistentes

## 0.6.2 (2026-03-17)



### Novas funcionalidades

• O script de instalação (setup.sh) agora salva automaticamente a porta do painel admin no arquivo de configuração global, eliminando a necessidade de configuração manual após a instalação

## 0.6.1 (2026-03-17)



### Novas funcionalidades

• A porta do painel admin agora é configurável através da variável ADMIN_PORT, permitindo escolher em qual porta o painel web será servido. Útil para ambientes onde a porta padrão já está em uso ou quando se deseja rodar múltiplas instâncias lado a lado.

## 0.6.0 (2026-03-17)

Novas funcionalidades

• Suporte completo a Docker: todos os pontos do sistema que dependiam de systemd agora funcionam também em containers Docker, permitindo rodar o SMB Claw em ambientes containerizados sem adaptações manuais
• Detecção automática do ambiente de execução — o framework identifica se está rodando em uma VPS com systemd ou em um container Docker e ajusta o gerenciamento de serviços adequadamente
• Flexibilidade de deploy ampliada: agora é possível escolher entre instalação direta na VPS ou deploy via Docker conforme a necessidade da sua infraestrutura

## 0.5.0 (2026-03-16)

Novas funcionalidades

• Comando /config permite gerenciar agentes e configurações globais diretamente pelo Telegram, sem precisar acessar o servidor via SSH ou painel web

Melhorias

• Terminologia unificada: todos os textos do wizard de criação e setup agora usam "agente" em vez de "bot", refletindo melhor a natureza dos assistentes criados pela plataforma
• Documentação reorganizada em diretório docs/ dedicado, facilitando a navegação e manutenção do projeto
• Nova tabela comparativa SMB Claw vs OpenClaw no README, ajudando na avaliação e escolha do framework

## 0.4.0 (2026-03-16)

Novas funcionalidades

• Wizard CLI interativo no setup.sh — cria e configura bots direto no terminal, sem editar arquivos manualmente
• Suporte a OAuth do Claude e OpenAI no wizard, permitindo usar ChatGPT Plus ou Claude Pro sem chave de API
• Três provedores disponíveis no wizard com submenus unificados: Anthropic, OpenRouter e OpenAI/Codex
• Auto-detecção de admin no primeiro /start — dispensa configuração manual do ADMIN_ID
• Auto-start do bot após criação pelo wizard, inclusive em ambientes Docker
• Suporte ao endpoint WHAM (Responses API da OpenAI) para autenticação via ChatGPT Plus OAuth
• Instalação automática de dependências do sistema (python3, pip, git, lsof)
• Detecção de ambiente Docker com aviso sobre portas não expostas
• Modo --config no setup.sh para editar configuração existente sem reinstalar
• Aviso de segurança durante o setup e seleção automática de porta alternativa quando a padrão está ocupada

Melhorias

• Diagnóstico aprimorado de falhas na inicialização de bots, com limpeza automática de locks órfãos e processos antigos
• Bots offline são reiniciados automaticamente

Correções

• Compatibilidade com SDK Anthropic 2.26 — streaming e extração de tool calls corrigidos
• Setup.sh não aborta mais inesperadamente durante a inicialização de bots
• Escape codes ANSI não vazam mais na saída do setup em ambientes Docker
• Detecção de pip3 como fallback quando pip não está disponível
• Inicialização de bots em Docker usa nohup em vez de systemd

## 0.3.0 (2026-03-16)

Novas funcionalidades

• Acesso temporário ao painel admin via token: agora é possível gerar links de acesso ao painel administrativo com validade configurável diretamente pelo comando /painel no Telegram. Os tokens são temporários, expiram automaticamente e não sobrevivem a reinicializações, garantindo segurança sem necessidade de senhas fixas.

## 0.2.2 (2026-03-15)

Novas funcionalidades

• Wizard interativo para criação de novos agentes Telegram diretamente pelo chat, sem necessidade de editar arquivos manualmente.
• Wizard interativo para criação de sub-agentes especializados, permitindo delegar tarefas a agentes dedicados com ferramentas e personalidades próprias.
• Comando /cancelar_wizard para interromper qualquer wizard em andamento a qualquer momento.
• Menu de comandos Telegram atualizado e organizado, exibindo todas as opções disponíveis para o usuário e para o administrador de forma clara.

Melhorias

• Proteção aprimorada contra uso indevido do sistema: bloqueio de tentativas de sobrescrever instruções de segurança via mensagens e comandos.
• Boas-vindas do /start revisadas para refletir todos os comandos disponíveis, incluindo os novos wizards de criação.
• Experiência do administrador mais fluida ao provisionar novos agentes — o processo agora guia passo a passo sem exigir acesso ao servidor.

## 0.2.1 (2026-03-15)

Com base nas mudanças reais do repositório, aqui estão as notas de release:

---

Novas funcionalidades

• Wizard interativo no Telegram para criar agentes e sub-agentes diretamente pelo chat, sem precisar acessar o servidor — basta usar /criar_agente ou /criar_subagente e responder as perguntas passo a passo.
• O wizard gera o soul.md automaticamente com ajuda do Claude a partir da descrição do agente, ou aceita um texto personalizado.
• Comando /apagar_agente para remover agentes existentes diretamente pelo Telegram.
• Seção de changelog público na landing page, exibindo o histórico de versões gerado automaticamente a cada release.
• Painel admin com editor visual do context.global — instruções globais compartilhadas entre todos os agentes, editáveis sem acessar o servidor.
• Variáveis de sistema protegidas no painel admin: chaves reservadas aparecem como somente leitura, evitando exclusão acidental.
• Notas de release geradas automaticamente com IA no release.sh, com fallback entre Claude OAuth, OpenRouter e OpenAI.
• Aba de status do memory-autosave no painel admin, com log das últimas execuções e botão para rodar manualmente.

Melhorias

• memory-autosave.sh reescrito com detecção automática de provedor e registro de estado persistente em .memory_autosave_state.
• Scheduler resiliente a flood control do Telegram: mensagens longas são divididas em chunks e reenvios aguardam o tempo indicado pelo Telegram antes de tentar novamente.

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
