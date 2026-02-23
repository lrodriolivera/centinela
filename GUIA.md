# Guía de Uso — Centinela

## 1. Inicio Rápido

```bash
cd ~/Documentos/agente_IA_Personal/centinela
source .venv/bin/activate
```

A partir de aquí, el comando `centinela` está disponible.

---

## 2. Chat Interactivo (CLI)

```bash
centinela chat
```

Se abre un chat en la terminal. El orquestador analiza tu mensaje y lo envía al agente más adecuado:

| Escribes | Agente que responde |
|---|---|
| "Hola, cómo estás?" | General (conversación) |
| "Lee el archivo main.py" | Coder (código) |
| "Ejecuta ls -la" | Executor (shell) |
| "Busca info sobre Docker" | Researcher (investigación) |
| "Revisa el código de config.py" | Reviewer (validación) |

### Comandos dentro del chat

| Comando | Acción |
|---|---|
| `/reset` | Reinicia la conversación |
| `/models` | Muestra estado de modelos |
| `/help` | Lista de comandos |
| `/q` o `salir` | Salir |
| `Ctrl+C` | Salir |

### Chat directo (sin orquestador)

```bash
# Un solo mensaje
centinela chat "Explícame qué es una API REST"

# Sin routing, directo al modelo primario
centinela chat --direct "Pregunta simple"

# Elegir modelo específico
centinela chat --model haiku "Responde brevemente: qué es Python?"
```

---

## 3. Web UI + API

```bash
centinela serve
```

Abre en el navegador: **http://127.0.0.1:8000/**

La Web UI tiene:
- **Chat** con streaming en tiempo real (WebSocket)
- **Agentes** — panel lateral con estado de cada agente
- **Auditoría** — visor de logs con severidad por colores

### Usar la API con curl

```bash
# 1. Obtener token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/token \
  -H "Content-Type: application/json" \
  -d '{"subject": "mi_usuario"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 2. Chat
curl -X POST http://127.0.0.1:8000/api/chat/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hola Centinela"}'

# 3. Ver modelos
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/models

# 4. Ver agentes
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/agents

# 5. Ver auditoría
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/audit?limit=10

# 6. Chat con streaming (SSE)
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Cuéntame sobre Python"}'
```

### Puerto y host personalizados

```bash
centinela serve --port 3000
centinela serve --host 0.0.0.0 --port 8080  # accesible desde la red local
```

---

## 4. Diagnóstico del Sistema

```bash
# Verificar que todo funciona
centinela doctor

# Estado de los 3 modelos Claude (Opus, Sonnet, Haiku)
centinela models

# Estado de agentes y memoria
centinela status

# Ver configuración actual
centinela config show

# Versión
centinela version
```

---

## 5. Auditoría

```bash
# Últimas 20 acciones
centinela audit

# Últimas 50
centinela audit --last 50
```

Muestra: timestamp, tipo de evento, agente, y detalles.
Los secretos (passwords, tokens, AWS keys) se redactan automáticamente.

---

## 6. Seguridad en Acción

### Cómo funciona la ejecución de comandos

Cuando le pides a Centinela que ejecute un comando, pasa por 3 filtros:

**Nivel 1 — Comandos seguros (se ejecutan inmediatamente):**
```
ls, cat, grep, find, git status, git log, git diff,
whoami, uname, date, df, jq, echo, sort, wc...
```

**Nivel 2 — Requieren tu aprobación (te pregunta antes):**
```
rm, mv, cp, curl, wget, pip install, npm install,
git push, docker run, python3, make, chmod...
```
Te aparece un prompt: `¿Aprobar? (s/n):`

**Nivel 3 — Bloqueados siempre (rechazados automáticamente):**
```
sudo, shutdown, reboot, rm -rf /, chmod 777,
dd, mkfs, curl|bash, acceso a ~/.ssh/, ~/.aws/...
```

### Sandbox Docker

Si tienes Docker, los comandos se ejecutan en un contenedor aislado:
- Sin acceso a red
- Filesystem read-only
- Sin capabilities de Linux
- Límite de memoria (512MB) y CPU
- Timeout (5 minutos)

Si Docker no está disponible, se ejecuta localmente con las mismas políticas de filtrado.

---

## 7. Bot de Telegram

### Configuración

1. Abre Telegram y busca `@BotFather`
2. Envía `/newbot` y sigue las instrucciones
3. Copia el token que te da

```bash
# Iniciar el bot
export CENTINELA_TELEGRAM_TOKEN="123456:ABC-DEF..."
centinela telegram
```

### Restringir acceso (opcional)

```bash
# Solo permitir ciertos chats (obtén tu chat_id enviando /start al bot)
export CENTINELA_TELEGRAM_ALLOWED_CHATS="123456789,987654321"
centinela telegram
```

### Comandos del bot

| Comando | Acción |
|---|---|
| `/start` | Bienvenida |
| `/status` | Estado del sistema |
| `/models` | Modelos disponibles |
| `/reset` | Reiniciar conversación |
| Cualquier texto | Chat con Centinela |

---

## 8. Bot de Slack

### Configuración

1. Ve a https://api.slack.com/apps y crea una app
2. En **OAuth & Permissions**, agrega scopes: `chat:write`, `app_mentions:read`, `im:history`
3. En **Socket Mode**, actívalo y genera un App-Level Token
4. Instala la app en tu workspace

```bash
export CENTINELA_SLACK_BOT_TOKEN="xoxb-..."
export CENTINELA_SLACK_APP_TOKEN="xapp-..."
centinela slack
```

### Uso en Slack

- **Mensaje directo** al bot → responde con Centinela
- **@Centinela** en un canal → responde en thread
- **`/centinela status`** → estado del sistema
- **`/centinela models`** → modelos disponibles
- **`/centinela reset`** → reiniciar conversación
- **`/centinela <pregunta>`** → chat directo

---

## 9. Modo Daemon (servicio de fondo)

### Instalar como servicio

```bash
# Instalar (genera el archivo de servicio)
centinela daemon install

# Iniciar
centinela daemon start

# Verificar estado
centinela daemon status

# Detener
centinela daemon stop

# Desinstalar
centinela daemon uninstall
```

En **Ubuntu** crea un servicio systemd de usuario.
En **macOS** crea un agente launchd.

### Daemon con bots

Si configuras las variables de entorno antes de iniciar, el daemon ejecuta todo junto:

```bash
export CENTINELA_TELEGRAM_TOKEN="..."
export CENTINELA_SLACK_BOT_TOKEN="..."
export CENTINELA_SLACK_APP_TOKEN="..."
centinela daemon start
# → Gateway API + Telegram bot + Slack bot, todo en un proceso
```

---

## 10. Configuración

### Archivo principal

Ubicación (por prioridad):
1. `./config/centinela.yaml` (directorio del proyecto)
2. `~/.centinela/centinela.yaml`
3. `/etc/centinela/centinela.yaml`

### Variables de entorno (override)

Todas usan prefijo `CENTINELA_` con `__` para niveles anidados:

```bash
# Cambiar región AWS
export CENTINELA_MODELS__REGION=eu-west-1

# Cambiar perfil AWS
export CENTINELA_MODELS__AWS_PROFILE=otro-perfil

# Cambiar puerto del gateway
export CENTINELA_GATEWAY__PORT=3000
```

### Modelos disponibles

| Alias | Modelo | Uso |
|---|---|---|
| `opus` | Claude Opus 4.5 | Primario — máxima capacidad |
| `sonnet` | Claude Sonnet 4.6 | Fallback 1 — equilibrio velocidad/capacidad |
| `haiku` | Claude Haiku 4.5 | Fallback 2 — más rápido, menor costo |

El sistema usa Opus por defecto. Si Opus falla (rate limit, timeout), automáticamente cambia a Sonnet, y luego a Haiku.

### Personalizar políticas de comandos

Edita `config/policies.yaml` para agregar o quitar comandos de cada categoría.

---

## 11. Estructura de Archivos

```
~/.centinela/                    ← Directorio de runtime
├── centinela.yaml               ← Config (copia personalizada)
├── logs/
│   └── audit.jsonl              ← Audit log
└── memory/
    ├── qdrant/                  ← Base de datos vectorial
    ├── transcripts/             ← Conversaciones por día
    │   └── 2026-02-23.jsonl
    └── preferences.yaml         ← Preferencias aprendidas
```

---

## 12. Resumen de Comandos

```
centinela chat [mensaje]          Chat interactivo o mensaje único
centinela chat --direct           Sin orquestador (directo al LLM)
centinela chat --model haiku      Elegir modelo específico
centinela serve                   API REST + Web UI
centinela serve --port 3000       Puerto personalizado
centinela doctor                  Verificar sistema
centinela models                  Estado de modelos
centinela status                  Agentes y memoria
centinela audit --last N          Últimas N acciones
centinela config show             Configuración actual
centinela version                 Versión
centinela daemon install          Instalar servicio
centinela daemon start            Iniciar daemon
centinela daemon stop             Detener daemon
centinela daemon status           Estado del daemon
centinela telegram                Bot de Telegram
centinela slack                   Bot de Slack
```
