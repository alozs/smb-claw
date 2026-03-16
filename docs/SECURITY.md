# Segurança

## Proteções do framework

- **Tokens isolados** — cada agente tem token único, com lock file que impede instâncias duplicadas
- **Credenciais protegidas** — `.env`, `secrets.env`, `secrets.global` com `chmod 600`
- **Shell sandbox** — denylist bloqueia `printenv`, `env`, `$ANTHROPIC_API_KEY`, `$TELEGRAM_TOKEN` etc.
- **Path traversal** — file tool sandboxado no `WORK_DIR` do agente
- **HTTP safety** — bloqueia IPs internos (169.254.x, localhost, metadata endpoints); resolve placeholders `$VAR` nos headers sem expor secrets
- **SQL safety** — bloqueia DROP/TRUNCATE sem WHERE
- **Git safety** — token injetado no URL em memória, nunca exibido nos logs
- **Markdown safety** — dados dinâmicos escapados para evitar erros de parse do Telegram
- **Controle de acesso** — modo `approval` (padrão) exige aprovação do admin para novos usuários
- **Drop pending updates** — mensagens acumuladas durante downtime são descartadas no restart

---

## Painel admin

### Autenticação por token temporário

O painel é protegido por tokens temporários — sem token válido, todas as rotas retornam **403**.

**Gerar link de acesso:**

```bash
# Via Telegram (admin only):
/painel        # link válido por 30 minutos (padrão)
/painel 60     # link válido por 60 minutos

# Via CLI (no servidor):
./gerar-acesso.sh       # 30 minutos (padrão)
./gerar-acesso.sh 60    # 60 minutos
```

O link gerado contém um token único (ex: `https://seu-dominio/?token=abc123...`). Ao acessar, um cookie de sessão é criado e o token é consumido. Após o tempo expirar, o acesso é revogado automaticamente.

**Configuração** (em `admin/.env.admin`):

| Variável | Padrão | Descrição |
|---|---|---|
| `ADMIN_PASSWORD` | *(obrigatório)* | Secret usado para assinar tokens (HMAC-SHA256) |
| `TOKEN_TTL` | `1800` | TTL padrão dos tokens em segundos (30 min) |

> Tokens são armazenados em memória — reiniciar o painel invalida todos os tokens ativos (comportamento intencional).

---

### Acesso remoto via HTTPS (opcional)

Por padrão o painel escuta apenas em `127.0.0.1:8080` (acesso local/SSH). Para acessar pela internet com segurança, configure um reverse proxy com HTTPS.

**Pré-requisitos:**
- Um domínio (ou subdomínio) apontando para o IP do servidor (registro DNS tipo A)
- Porta 443 liberada no firewall

**Exemplo com Caddy** (HTTPS automático via Let's Encrypt):

```bash
# 1. Instalar Caddy
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

# 2. Configurar (substitua pelo seu domínio)
echo 'seudominio.com {
    reverse_proxy 127.0.0.1:8080
}' | sudo tee /etc/caddy/Caddyfile

# 3. Reiniciar Caddy
sudo systemctl restart caddy

# 4. Verificar
sudo systemctl status caddy
```

O Caddy emite e renova o certificado SSL automaticamente. Após configurar, acesse via `https://seudominio.com` usando o link gerado pelo `/painel` ou `gerar-acesso.sh`.

**Firewall recomendado (UFW):**

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP (redirect → HTTPS)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw --force enable
```

> **Importante:** nunca exponha a porta 8080 diretamente na internet — sempre use HTTPS via reverse proxy.
