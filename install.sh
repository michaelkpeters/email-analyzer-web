#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/sublime-analyzer"
SERVICE_NAME="sublime-analyzer"
USER_NAME="analyzer"

# Detect if we are in a git repo or curl'd standalone
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo " Sublime Email Analyzer — Install Script"
echo "========================================"

# ---------------------------------------------------------
# 1. Install system dependencies
# ---------------------------------------------------------
echo "[*] Updating packages and installing dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl git

# ---------------------------------------------------------
# 2. Create dedicated user
# ---------------------------------------------------------
if ! id -u "$USER_NAME" &>/dev/null; then
    echo "[*] Creating user '$USER_NAME'..."
    useradd -r -m -s /bin/false "$USER_NAME"
else
    echo "[*] User '$USER_NAME' already exists."
fi

# ---------------------------------------------------------
# 3. Copy / update application files
# ---------------------------------------------------------
echo "[*] Installing application to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# If running from a cloned repo, copy everything except venv/dotfiles
if [ -f "$SCRIPT_DIR/app/main.py" ]; then
    rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
          "$SCRIPT_DIR/" "$INSTALL_DIR/"
else
    echo "[!] Could not find app/main.py in $SCRIPT_DIR"
    echo "    Please run this script from the project root."
    exit 1
fi

chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

# ---------------------------------------------------------
# 4. Create Python virtual environment
# ---------------------------------------------------------
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "[*] Creating Python venv..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "[*] Installing Python dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ---------------------------------------------------------
# 5. Install systemd service
# ---------------------------------------------------------
echo "[*] Installing systemd service..."
cp "$INSTALL_DIR/sublime-analyzer.service" /etc/systemd/system/

# Update ExecStart in case port/path changes were made
# (The service file is already correct for defaults)

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# ---------------------------------------------------------
# 6. Start service
# ---------------------------------------------------------
echo "[*] Starting $SERVICE_NAME..."
systemctl restart "$SERVICE_NAME"

# ---------------------------------------------------------
# 7. Verify
# ---------------------------------------------------------
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "========================================"
    IP=$(hostname -I | awk '{print $1}')
    echo " ✅ Installation complete!"
    echo ""
    echo "    URL: http://${IP}:8000"
    echo ""
    echo "    Logs: journalctl -u $SERVICE_NAME -f"
    echo "    Stop:  systemctl stop $SERVICE_NAME"
    echo "    Start: systemctl start $SERVICE_NAME"
    echo "========================================"
else
    echo ""
    echo " [!] Service failed to start. Check logs:"
    echo "     journalctl -u $SERVICE_NAME -n 50 --no-pager"
    exit 1
fi
