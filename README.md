# Sublime Security Email Analyzer (Web Installable)

A self-contained, installable web application that accepts Outlook `.msg` or `.eml` email files and analyzes them with the **free** Sublime Security Analyzer API at [https://analyzer.sublime.security](https://analyzer.sublime.security).

Built for minimal setup and maintenance — ideal for running inside a Proxmox LXC container or any Debian/Ubuntu server.

---

## 🚀 Quickest Start — Proxmox VE Helper Script (One-Liner)

Run this directly on your **Proxmox host shell** (as `root`). It will:
1. Create an optimized Ubuntu 24.04 LXC container
2. Download and install the app automatically
3. Start the service and print the URL

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/michaelkpeters/email-analyzer-web/main/proxmox-helper.sh)"
```

> The one-liner below points to **your** repo (`michaelkpeters/email-analyzer-web`).

### Local / Offline Helper Script

If you already cloned the repo:

```bash
cd /opt/sublime-analyzer
sudo bash proxmox-helper.sh
```

**Interactive prompts inside the helper:**
- Container ID, hostname, memory, cores, disk size
- Static IP or DHCP
- **Docker** or **native systemd** install inside the LXC

---

## 📦 Manual Quick Start (Docker)

```bash
cd /opt
git clone https://github.com/michaelkpeters/email-analyzer-web.git sublime-analyzer
cd sublime-analyzer
docker compose up -d
```

Open `http://<server-ip>:8000`

---

## 🖥️ Manual Quick Start (Native / No Docker)

```bash
cd /opt
git clone https://github.com/michaelkpeters/email-analyzer-web.git sublime-analyzer
cd sublime-analyzer
chmod +x install.sh
sudo ./install.sh
```

The install script will:
1. Install `python3`, `python3-venv`
2. Create a dedicated `analyzer` user
3. Set up a Python virtual environment
4. Install pinned dependencies
5. Install and start a `systemd` service
6. Print the URL to access the app

---

## 🔄 Updating

### With the helper script (re-run inside the LXC)
```bash
pct exec <CTID> -- bash -c "cd /opt/sublime-analyzer && git pull && ./install.sh"
```

### Docker
```bash
cd /opt/sublime-analyzer
git pull
docker compose up -d --build
```

### Native
```bash
cd /opt/sublime-analyzer
git pull
sudo ./install.sh   # reinstalls deps and restarts service
```

---

## ⚙️ Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `PORT` | `8000` | HTTP port to listen on |
| `SUBLIME_API_BASE` | `https://analyzer.sublime.security` | Analyzer API endpoint |

No API key is required. The free Analyzer API is unauthenticated.

---

## 📁 Project Structure

```
.
├── Dockerfile                 # Container build
├── docker-compose.yml         # Container orchestration
├── install.sh                 # Automated native install
├── proxmox-helper.sh          # Proxmox VE one-liner helper
├── sublime-analyzer.service   # systemd unit template
├── requirements.txt           # Pinned Python deps
├── README.md                  # This file
└── app/
    ├── main.py                # FastAPI entry point
    ├── msg_converter.py       # .msg → .eml conversion
    ├── rule_scanner.py        # Sublime Analyzer API client
    ├── models.py              # Pydantic schemas
    └── static/
        └── index.html         # Drag-and-drop UI
```

---

## 🔧 Troubleshooting

**Service won't start**
```bash
journalctl -u sublime-analyzer -n 50 --no-pager
```

**Port already in use**
```bash
# Edit the service or docker-compose.yml to change the port
export PORT=8080
```

**Container not reachable from outside Proxmox**
- Ensure the LXC network bridge (`vmbr0`) is correct.
- Check Proxmox firewall rules for the container.
- Verify the port is listening inside the container: `ss -tlnp | grep 8000`

---

## License

Same as the original project.
