# Painel Admin (Web)

Dashboard FastAPI para gerenciar todo o sistema via navegador.

```bash
# Já iniciado pelo setup.sh, ou manualmente:
bash setup.sh
# Acesse http://<ip>:8080
```

## Funcionalidades

- **Dashboard** — visão geral de todos os agentes (status, uptime)
- **Setup wizard** — configuração guiada na primeira execução
- **Editor de configuração** — `.env`, `soul.md`, `USER.md`, `MEMORY.md`, `context.global`, `config.global`
- **Gerenciamento de serviços** — start, stop, restart via interface
- **Logs em tempo real** — streaming de journalctl no navegador
- **Criação/exclusão de agentes** — com validação e setup automático do systemd
- **Upload de avatar** — imagem personalizada por agente
- **Gerenciamento de usuários** — aprovados, analytics, conversas
- **Agendamentos** — criar, listar e remover notificações proativas
- **Envio de mensagens** — enviar mensagem direta ou broadcast para todos os usuários
- **Exportação** — download de conversas e dados
- **Subagentes** — criar, editar e remover subagentes
- **Bug Fixer** — configurar, executar e ver logs do agente autônomo
- **Crontab** — editor visual do crontab do sistema

---

## API REST

Todas as operações são expostas como API REST em `/api/`:

| Endpoint | Método | Descrição |
|---|---|---|
| `/api/bots` | GET | Lista todos os agentes |
| `/api/bots` | POST | Cria novo agente |
| `/api/bots/{name}` | GET/DELETE | Detalhes / remove agente |
| `/api/bots/{name}/env` | GET/PUT | Configuração .env |
| `/api/bots/{name}/file/{fname}` | GET/PUT | Editar soul.md, USER.md etc. |
| `/api/bots/{name}/action` | POST | start/stop/restart serviço |
| `/api/bots/{name}/logs` | GET | Stream de logs (SSE) |
| `/api/bots/{name}/analytics` | GET | Métricas de uso |
| `/api/bots/{name}/users` | GET | Usuários aprovados |
| `/api/bots/{name}/schedules` | GET/POST/DELETE | Agendamentos |
| `/api/bots/{name}/send-message` | POST | Enviar mensagem direta |
| `/api/bots/{name}/broadcast` | POST | Broadcast para todos |
| `/api/bots/{name}/avatar` | GET/POST/DELETE | Avatar do agente |
| `/api/global/{fname}` | GET/PUT | Arquivos globais |
| `/api/subagents` | GET/POST/DELETE | Gerenciar subagentes |
| `/api/setup/*` | POST | Setup wizard |
| `/api/system/bugfixer` | GET/PUT/POST | Bug Fixer Agent |
| `/api/crontab` | GET/PUT | Crontab do sistema |
| `/api/gen-token` | POST | Gera token de acesso temporário (apenas localhost) |

---

## Acesso e segurança

Veja [SECURITY.md](SECURITY.md) para detalhes sobre autenticação por token, HTTPS e firewall.
