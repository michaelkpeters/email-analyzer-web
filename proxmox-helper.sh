#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Proxmox VE Helper Script — Sublime Security Email Analyzer
# =============================================================================
# This script can run in two modes:
#   1. On the Proxmox host → creates an LXC and installs the app inside it
#   2. Inside an LXC/container → installs the app directly (native systemd)
#
# One-liner from the Proxmox host shell:
#   bash -c "$(wget -qLO - https://raw.githubusercontent.com/YOURREPO/main/proxmox-helper.sh)"
#
# Or clone and run locally:
#   git clone <repo> /opt/sublime-analyzer
#   cd /opt/sublime-analyzer && sudo bash proxmox-helper.sh
# =============================================================================

# --- Colors ------------------------------------------------------------------
RD='\e[31m'
GN='\e[32m'
YW='\e[33m'
BL='\e[34m'
CL='\e[0m'
CM='\e[96m'

msg_info() {
  echo -e "${BL}[INFO]${CL}  $1"
}
msg_ok() {
  echo -e "${GN}[OK]${CL}    $1"
}
msg_warn() {
  echo -e "${YW}[WARN]${CL}  $1"
}
msg_error() {
  echo -e "${RD}[ERROR]${CL} $1"
}
msg_step() {
  echo -e "${CM}▶ $1${CL}"
}

# --- Detect environment ------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="sublime-analyzer"
INSTALL_DIR="/opt/${APP_NAME}"
REPO_URL="${REPO_URL:-https://github.com/YOURUSER/sublime-analyzer-web.git}"
# ^^^ Update this to your actual GitHub/GitLab/self-hosted repo URL before using.
# Or override at runtime: REPO_URL=https://your.repo/url.git bash proxmox-helper.sh
IS_PROXMOX_HOST=false
if command -v pct &>/dev/null && [ -d /etc/pve ]; then
  IS_PROXMOX_HOST=true
fi

# --- Prompt / args -----------------------------------------------------------
CT_ID=""
CT_IP=""
CT_GW=""
CT_STORAGE="local-lvm"
CT_TEMPLATE="local:vztmpl/ubuntu-24.04-standard_24.04-1_amd64.tar.zst"
USE_DOCKER=false

show_banner() {
  clear
  cat <<'EOF'
   _____       _     _ _ _           _      _              _
  / ____|     | |   | | (_)         | |    | |            | |
 | (___  _   _| |__ | | |_ _ __ ___ | | ___| |_ ___   ___ | |___
  \___ \| | | | '_ \| | | | '_ ` _ \| |/ _ \ __/ _ \ / _ \| / __|
  ____) | |_| | |_) | | | | | | | | | |  __/ || (_) | (_) | \__ \
 |_____/ \__,_|_.__/|_|_|_|_| |_| |_|_|\___|\__\___/ \___/|_|___/

EOF
  echo -e "${CM}   Proxmox VE Helper Script — Sublime Security Email Analyzer${CL}\n"
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local answer
  while true; do
    if [ "$default" = "y" ]; then
      read -rp "$prompt [Y/n]: " answer
      answer="${answer:-Y}"
    else
      read -rp "$prompt [y/N]: " answer
      answer="${answer:-N}"
    fi
    case "$answer" in
      [Yy]*) return 0 ;;
      [Nn]*) return 1 ;;
    esac
  done
}

# --- Proxmox host: create LXC -----------------------------------------------
proxmox_create_lxc() {
  msg_step "Creating LXC container on Proxmox host"

  if [ "$EUID" -ne 0 ]; then
    msg_error "This script must run as root on the Proxmox host."
    exit 1
  fi

  # Choose next available ID if not set
  if [ -z "$CT_ID" ]; then
    CT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "201")
    read -rp "Container ID [$CT_ID]: " input
    CT_ID="${input:-$CT_ID}"
  fi

  # Check if CT already exists
  if pct status "$CT_ID" &>/dev/null; then
    msg_error "Container $CT_ID already exists."
    exit 1
  fi

  read -rp "Storage pool [local-lvm]: " input
  CT_STORAGE="${input:-local-lvm}"

  read -rp "Root disk size (GB) [8]: " input
  DISK_SIZE="${input:-8}"

  read -rp "Memory (MB) [1024]: " input
  MEMORY="${input:-1024}"

  read -rp "Cores [2]: " input
  CORES="${input:-2}"

  read -rp "Hostname [sublime-analyzer]: " input
  HOSTNAME="${input:-sublime-analyzer}"

  read -rp "Network: static IP with CIDR (e.g. 192.168.1.100/24) [dhcp]: " input
  CT_IP="${input:-dhcp}"

  if [ "$CT_IP" != "dhcp" ]; then
    read -rp "Gateway [192.168.1.1]: " input
    CT_GW="${input:-192.168.1.1}"
  fi

  if ask_yes_no "Install Docker inside the LXC?" "n"; then
    USE_DOCKER=true
  fi

  # Find template
  msg_info "Looking for Ubuntu 24.04 template..."
  TEMPLATE_LIST=$(pveam available 2>/dev/null | grep ubuntu-24.04-standard | awk '{print $2}' | sort -V | tail -1)
  if [ -z "$TEMPLATE_LIST" ]; then
    msg_warn "Template not found locally. Updating template list..."
    pveam update
    TEMPLATE_LIST=$(pveam available 2>/dev/null | grep ubuntu-24.04-standard | awk '{print $2}' | sort -V | tail -1)
  fi
  if [ -z "$TEMPLATE_LIST" ]; then
    msg_error "Could not find an Ubuntu 24.04 template. Please download one in Proxmox first."
    exit 1
  fi

  if ! pveam list local | grep -q "$TEMPLATE_LIST"; then
    msg_info "Downloading template $TEMPLATE_LIST..."
    pveam download local "$TEMPLATE_LIST"
  fi
  CT_TEMPLATE="local:vztmpl/${TEMPLATE_LIST}"

  # Build features string
  FEATURES="nesting=1"
  if [ "$USE_DOCKER" = true ]; then
    FEATURES="${FEATURES},keyctl=1"
  fi

  # Build network string
  if [ "$CT_IP" = "dhcp" ]; then
    NET_STR="name=eth0,bridge=vmbr0,ip=dhcp"
  else
    NET_STR="name=eth0,bridge=vmbr0,ip=${CT_IP},gw=${CT_GW}"
  fi

  msg_info "Creating container $CT_ID..."
  pct create "$CT_ID" "$CT_TEMPLATE" \
    --hostname "$HOSTNAME" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --rootfs "${CT_STORAGE}:${DISK_SIZE}" \
    --net0 "$NET_STR" \
    --unprivileged 1 \
    --features "$FEATURES" \
    --onboot 1 \
    --ostype ubuntu

  msg_ok "Container $CT_ID created."

  msg_info "Starting container $CT_ID..."
  pct start "$CT_ID"
  sleep 5

  # Wait for network
  msg_info "Waiting for container network..."
  for i in {1..30}; do
    if pct exec "$CT_ID" -- bash -c "ip addr show eth0 | grep 'inet '" &>/dev/null; then
      break
    fi
    sleep 1
  done

  # Determine actual IP
  ACTUAL_IP=$(pct exec "$CT_ID" -- bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null || echo "")
  if [ -z "$ACTUAL_IP" ]; then
    ACTUAL_IP="<container-ip>"
  fi

  msg_ok "Container is running at ${ACTUAL_IP}"

  # --- Install inside the LXC ---
  msg_step "Installing Sublime Analyzer inside container $CT_ID"

  # Prepare install commands to run inside CT
  INSTALL_CMD=$(cat <<EOF
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git wget rsync

# If Docker mode was chosen, install Docker
if [ "$USE_DOCKER" = "true" ]; then
  curl -fsSL https://get.docker.com | sh
  apt-get install -y -qq docker-compose-plugin
fi

# Clone repo
mkdir -p /opt
if [ -d /opt/${APP_NAME} ]; then
  cd /opt/${APP_NAME} && git pull
else
  git clone ${REPO_URL} /opt/${APP_NAME}
fi

cd /opt/${APP_NAME}

if [ "$USE_DOCKER" = "true" ]; then
  docker compose up -d
else
  bash install.sh
fi
EOF
)

  msg_info "Running installation inside container (this may take a few minutes)..."
  pct exec "$CT_ID" -- bash -c "$INSTALL_CMD"

  msg_ok "Installation complete inside container $CT_ID!"
  echo ""
  echo -e "${GN}╔══════════════════════════════════════════════════════════════╗${CL}"
  echo -e "${GN}║               INSTALLATION COMPLETE                          ║${CL}"
  echo -e "${GN}╠══════════════════════════════════════════════════════════════╣${CL}"
  if [ "$USE_DOCKER" = true ]; then
    echo -e "${GN}║  Access URL:  http://${ACTUAL_IP}:8000                       ║${CL}"
    echo -e "${GN}║  Container:   $CT_ID                                         ║${CL}"
    echo -e "${GN}║  Docker:      docker compose up -d                           ║${CL}"
  else
    echo -e "${GN}║  Access URL:  http://${ACTUAL_IP}:8000                       ║${CL}"
    echo -e "${GN}║  Container:   $CT_ID                                         ║${CL}"
    echo -e "${GN}║  Logs:       pct exec $CT_ID -- journalctl -u $APP_NAME -f ║${CL}"
    echo -e "${GN}║  Restart:     pct exec $CT_ID -- systemctl restart $APP_NAME ║${CL}"
  fi
  echo -e "${GN}╚══════════════════════════════════════════════════════════════╝${CL}"
  echo ""
}

# --- Direct install (inside LXC or bare metal) -------------------------------
direct_install() {
  msg_step "Installing Sublime Analyzer directly on this system"

  if [ "$EUID" -ne 0 ]; then
    msg_error "This script must run as root. Use: sudo bash proxmox-helper.sh"
    exit 1
  fi

  export DEBIAN_FRONTEND=noninteractive

  msg_info "Updating package list..."
  apt-get update -qq

  msg_info "Installing prerequisites (git, curl, python3, rsync)..."
  apt-get install -y -qq git curl wget rsync python3 python3-venv

  # Clone or update repo
  if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
    msg_info "Using local files from ${SCRIPT_DIR}..."
    mkdir -p "$INSTALL_DIR"
    rsync -a --exclude='.git' --exclude='__pycache__' "$SCRIPT_DIR/" "$INSTALL_DIR/"
  else
    msg_info "Cloning from ${REPO_URL}..."
    if [ -d "$INSTALL_DIR" ]; then
      cd "$INSTALL_DIR" && git pull
    else
      git clone "$REPO_URL" "$INSTALL_DIR"
    fi
  fi

  cd "$INSTALL_DIR"

  msg_info "Running install.sh..."
  bash install.sh

  IP=$(hostname -I | awk '{print $1}')
  msg_ok "Installation complete!"
  echo ""
  echo -e "${GN}╔══════════════════════════════════════════════════════════════╗${CL}"
  echo -e "${GN}║               INSTALLATION COMPLETE                          ║${CL}"
  echo -e "${GN}╠══════════════════════════════════════════════════════════════╣${CL}"
  echo -e "${GN}║  Access URL:  http://${IP}:8000                              ║${CL}"
  echo -e "${GN}║  Install dir: ${INSTALL_DIR}                                 ║${CL}"
  echo -e "${GN}║  Logs:        journalctl -u ${APP_NAME} -f                   ║${CL}"
  echo -e "${GN}╚══════════════════════════════════════════════════════════════╝${CL}"
  echo ""
}

# --- Main --------------------------------------------------------------------
show_banner

if [ "$IS_PROXMOX_HOST" = true ]; then
  echo -e "${CM}Proxmox host detected.${CL}\n"
  echo "What would you like to do?"
  echo "  1) Create a new LXC container and install the app inside it"
  echo "  2) Install directly on the Proxmox host (not recommended)"
  echo "  3) Install into an existing LXC container"
  echo ""
  read -rp "Choice [1]: " choice
  choice="${choice:-1}"

  case "$choice" in
    1)
      proxmox_create_lxc
      ;;
    2)
      direct_install
      ;;
    3)
      read -rp "Existing Container ID: " CT_ID
      msg_info "Installing inside existing container $CT_ID..."
      pct exec "$CT_ID" -- bash -c "
        set -e
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq git curl wget rsync
        mkdir -p /opt
        if [ -d /opt/${APP_NAME} ]; then
          cd /opt/${APP_NAME} && git pull
        else
          git clone ${REPO_URL} /opt/${APP_NAME}
        fi
        cd /opt/${APP_NAME} && bash install.sh
      "
      IP=$(pct exec "$CT_ID" -- hostname -I | awk '{print $1}')
      msg_ok "Done! Access the app at http://${IP}:8000"
      ;;
    *)
      msg_error "Invalid choice. Exiting."
      exit 1
      ;;
  esac
else
  echo -e "${CM}Running inside a container / bare-metal system.${CL}\n"
  if ask_yes_no "Proceed with direct installation?" "y"; then
    direct_install
  else
    msg_info "Exiting."
    exit 0
  fi
fi
