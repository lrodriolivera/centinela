# Centinela

Agente IA autónomo con seguridad de grado empresarial. Desarrollado en Python, usa AWS Bedrock (Claude Opus 4.5 / Sonnet 4.6 / Haiku 4.5) con arquitectura multi-agente, sandbox Docker, y múltiples interfaces.

## Arquitectura

```
CLI / API REST / Web UI / Telegram / Slack
              │
        FastAPI Gateway (JWT auth, rate limiting, CORS)
              │
        Orquestador (router de intent via Haiku)
              │
   ┌──────┬───┴────┬──────────┐
   Coder  Research  Executor  Reviewer
              │
     Docker Sandbox (no-network, read-only, cap-drop ALL)
              │
     AWS Bedrock: Opus 4.5 → Sonnet 4.6 → Haiku 4.5
```

## Instalación

```bash
git clone https://github.com/lrodriolivera/centinela.git
cd centinela
./scripts/setup.sh
```

O manualmente:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
centinela doctor
```

## Uso

```bash
# Chat interactivo (multi-agente)
centinela chat

# Chat directo (sin orquestador)
centinela chat --direct "Hola"

# Gateway API + Web UI
centinela serve
# → http://127.0.0.1:8000/

# Ver estado
centinela doctor
centinela status
centinela models

# Auditoría
centinela audit --last 20

# Daemon (servicio de fondo)
centinela daemon install
centinela daemon start
centinela daemon status

# Bots de mensajería
CENTINELA_TELEGRAM_TOKEN=xxx centinela telegram
CENTINELA_SLACK_BOT_TOKEN=xxx CENTINELA_SLACK_APP_TOKEN=xxx centinela slack
```

## Seguridad

- **Sandbox Docker**: network disabled, read-only root, cap-drop ALL, no-new-privileges, pids limit
- **Policy engine**: 30+ comandos seguros, 14 regex patterns bloqueados (fork bombs, pipe-to-bash, acceso a ~/.ssh/, ~/.aws/)
- **JWT tokens**: efímeros, bound a IP+User-Agent (previene token theft)
- **Audit logging**: JSONL con redacción automática de secretos (AWS keys, tokens, passwords)
- **Permisos 4-tier**: READ / WRITE / EXECUTE / ADMIN por agente
- **Human-in-the-loop**: aprobación requerida para comandos peligrosos
- **systemd hardening**: NoNewPrivileges, ProtectSystem=strict, ProtectHome=read-only, PrivateTmp

## API

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/health` | GET | Health check (público) |
| `/api/token` | POST | Generar JWT token |
| `/api/chat` | POST | Chat streaming (SSE) |
| `/api/chat/sync` | POST | Chat síncrono |
| `/api/ws/chat` | WS | WebSocket chat |
| `/api/models` | GET | Estado de modelos |
| `/api/agents` | GET | Estado de agentes |
| `/api/audit` | GET | Logs de auditoría |
| `/api/pending` | GET | Acciones pendientes |
| `/api/approve/{id}` | POST | Aprobar acción |

## Tests

```bash
pytest -v          # 120+ tests
pytest --cov       # Con cobertura
```

## Configuración

Archivo principal: `~/.centinela/centinela.yaml` o `config/centinela.yaml`

Variables de entorno (override):
- `CENTINELA_MODELS__REGION` — Región AWS
- `CENTINELA_MODELS__AWS_PROFILE` — Perfil AWS CLI
- `CENTINELA_TELEGRAM_TOKEN` — Token del bot de Telegram
- `CENTINELA_TELEGRAM_ALLOWED_CHATS` — Chat IDs autorizados (csv)
- `CENTINELA_SLACK_BOT_TOKEN` — Token del bot de Slack
- `CENTINELA_SLACK_APP_TOKEN` — Token app-level de Slack

## Licencia

MIT
