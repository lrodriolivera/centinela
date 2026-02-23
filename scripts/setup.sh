#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Centinela — Setup Script (Ubuntu + macOS)
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[✗]${NC} $*"; }
log_step()  { echo -e "${CYAN}[→]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CENTINELA_HOME="$HOME/.centinela"

detect_os() {
    case "$(uname -s)" in
        Linux*)  OS="linux";;
        Darwin*) OS="macos";;
        *)       log_error "OS no soportado: $(uname -s)"; exit 1;;
    esac
    log_info "Sistema operativo: $OS"
}

check_python() {
    log_step "Verificando Python..."
    if ! command -v python3 &>/dev/null; then
        log_error "Python3 no encontrado."
        if [ "$OS" = "linux" ]; then
            log_error "Instala con: sudo apt install python3 python3-pip python3-venv"
        else
            log_error "Instala con: brew install python@3.12"
        fi
        exit 1
    fi

    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
        log_error "Python $PY_VERSION detectado. Se requiere 3.12+."
        exit 1
    fi

    log_info "Python $PY_VERSION"
}

check_docker() {
    log_step "Verificando Docker..."
    if ! command -v docker &>/dev/null; then
        log_warn "Docker no encontrado. El sandbox no estará disponible."
        log_warn "Instala Docker para habilitar ejecución aislada."
        return
    fi

    if ! docker info &>/dev/null; then
        log_warn "Docker no está corriendo. Inícialo para usar el sandbox."
        return
    fi

    DOCKER_VERSION=$(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)
    log_info "Docker $DOCKER_VERSION"
}

check_aws() {
    log_step "Verificando AWS CLI y perfil bedrock..."
    if ! command -v aws &>/dev/null; then
        log_error "AWS CLI no encontrado."
        log_error "Instala: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        exit 1
    fi

    if aws sts get-caller-identity --profile bedrock &>/dev/null; then
        ACCOUNT=$(aws sts get-caller-identity --profile bedrock --query Account --output text 2>/dev/null)
        log_info "AWS perfil 'bedrock' verificado (cuenta: $ACCOUNT)"
    else
        log_error "AWS perfil 'bedrock' no funciona."
        log_error "Configura con: aws configure --profile bedrock"
        exit 1
    fi
}

create_centinela_dirs() {
    log_step "Creando directorios de Centinela..."
    mkdir -p "$CENTINELA_HOME"/{logs,memory/qdrant,memory/transcripts}
    chmod 700 "$CENTINELA_HOME"
    log_info "Directorios creados en $CENTINELA_HOME"
}

secure_credentials() {
    log_step "Verificando seguridad de credenciales..."

    # Check if credentials are in the workspace (DANGEROUS)
    WORKSPACE_CREDS="$PROJECT_DIR/../Credenciales"
    if [ -d "$WORKSPACE_CREDS" ]; then
        log_warn "Credenciales encontradas dentro del workspace."
        log_warn "Moviendo a $HOME/.credentials-backup-openclaw/"
        mv "$WORKSPACE_CREDS" "$HOME/.credentials-backup-openclaw"
        chmod 700 "$HOME/.credentials-backup-openclaw"
        find "$HOME/.credentials-backup-openclaw" -type f -exec chmod 600 {} \;
        log_info "Credenciales movidas fuera del workspace."
    fi

    # Lock AWS credentials
    if [ -f "$HOME/.aws/credentials" ]; then
        chmod 700 "$HOME/.aws" 2>/dev/null || true
        chmod 600 "$HOME/.aws/credentials" "$HOME/.aws/config" 2>/dev/null || true
        log_info "Permisos de ~/.aws/ asegurados"
    fi
}

install_centinela() {
    log_step "Instalando Centinela..."
    cd "$PROJECT_DIR"

    # Create venv if not exists
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
        log_info "Entorno virtual creado"
    fi

    # Activate venv
    source .venv/bin/activate

    # Install
    pip install -e ".[dev]" --quiet
    log_info "Centinela instalado en modo desarrollo"
}

copy_default_config() {
    log_step "Configuración..."
    if [ ! -f "$CENTINELA_HOME/centinela.yaml" ]; then
        cp "$PROJECT_DIR/config/centinela.yaml" "$CENTINELA_HOME/centinela.yaml"
        chmod 600 "$CENTINELA_HOME/centinela.yaml"
        log_info "Configuración copiada a $CENTINELA_HOME/centinela.yaml"
    else
        log_info "Configuración existente preservada"
    fi
}

verify() {
    log_step "Verificación final..."
    source "$PROJECT_DIR/.venv/bin/activate"

    echo ""
    centinela doctor
    echo ""
    centinela version
}

main() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║     Centinela — Setup Seguro             ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
    echo ""

    detect_os
    check_python
    check_docker
    check_aws
    secure_credentials
    create_centinela_dirs
    install_centinela
    copy_default_config
    verify

    echo ""
    log_info "Setup completado."
    echo ""
    echo -e "  Para usar Centinela:"
    echo -e "    ${CYAN}source .venv/bin/activate${NC}"
    echo -e "    ${CYAN}centinela chat${NC}"
    echo ""
}

main "$@"
