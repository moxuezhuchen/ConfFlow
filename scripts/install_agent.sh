#!/usr/bin/env bash
# =============================================================================
# ConfFlow Agent Installation Script
# Implements a 5-tier deployment fallback chain, in order of preference:
#   1. systemd --user + lingering  (persistent, survives SSH)
#   2. systemd --user (no lingering, SSH-session-scoped)
#   3. supervisord              (process manager, needs pip install)
#   4. nohup + setsid            (manual, fallback)
#   5. Docker                    (Stage 5 / HPC container use, just notes)
#
# Each tier is attempted in order; the first one that succeeds is used.
# No tier is silently skipped — every failure is reported.
# =============================================================================

set -euo pipefail

AGENT_NAME="confflow-agent"
AGENT_USER="${SUDO_USER:-$USER}"
AGENT_HOME_DIR="$(eval echo ~"$AGENT_USER")"
QUEUE_DIR="${AGENT_HOME_DIR}/confflow-queue"
STATE_DB="${AGENT_HOME_DIR}/.local/share/${AGENT_NAME}/state.db"
LOG_DIR="${AGENT_HOME_DIR}/.local/log/${AGENT_NAME}"
SYSTEMD_UNIT="${AGENT_HOME_DIR}/.config/systemd/user/${AGENT_NAME}.service"
UNIT_SOURCE="${SCRIPT_DIR:-.}"/confflow-agent.service
SLOTS="${SLOTS:-2}"
# Where confflow-agent binary lives after pip install -e
AGENT_BIN="${AGENT_HOME_DIR}/.local/bin/${AGENT_NAME}"

# Colours for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Colour

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${GREEN}==== $* ====${NC}"; }

# ---- Helpers -----------------------------------------------------------------

detect_init() {
    if command -v systemctl &>/dev/null && systemctl --version &>/dev/null; then
        echo "systemd"
    elif command -v supervisord &>/dev/null; then
        echo "supervisord"
    else
        echo "nohup"
    fi
}

check_fs_type() {
    local dir="$1"
    # Try to detect NFS. NFS mounts often have lock issues with SQLite.
    if command -v df &>/dev/null; then
        local fstype
        fstype=$(df -T "$dir" 2>/dev/null | awk 'NR==2 {print $2}')
        if [[ "$fstype" == "nfs"* ]] || [[ "$fstype" == "nfs4" ]]; then
            warn "Queue directory $dir is on NFS ($fstype). SQLite may have lock issues."
            warn "Consider using a local filesystem for the queue directory."
            return 1
        fi
    fi
    return 0
}

ensure_dirs() {
    mkdir -p "$(dirname "$STATE_DB")"
    mkdir -p "$QUEUE_DIR"/{incoming,pending,done,status}
    mkdir -p "$LOG_DIR"
    info "Created directories under $AGENT_HOME_DIR"
}

# ---- Tier 1: systemd --user + lingering ---------------------------------------

install_systemd_lingering() {
    section "Tier 1: systemd --user + lingering"

    if ! command -v systemctl &>/dev/null; then
        error "systemd not available, skipping Tier 1"
        return 1
    fi

    if ! loginctl --version &>/dev/null; then
        error "loginctl not available, skipping Tier 1"
        return 1
    fi

    if ! loginctl enable-linger "$AGENT_USER" 2>&1; then
        error "Failed to enable lingering for $AGENT_USER (may need root)"
        return 1
    fi
    info "Lingering enabled for $AGENT_USER"

    # Copy unit file
    if [[ -f "$UNIT_SOURCE" ]]; then
        mkdir -p "$(dirname "$SYSTEMD_UNIT")"
        sed "s|%%SLOTS%%|${SLOTS}|g" "$UNIT_SOURCE" > "$SYSTEMD_UNIT"
        info "Installed systemd unit at $SYSTEMD_UNIT"
    else
        warn "Unit source $UNIT_SOURCE not found, creating unit inline"
        mkdir -p "$(dirname "$SYSTEMD_UNIT")"
        cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=ConfFlow Workflow Agent
After=network.target

[Service]
Type=simple
ExecStart=${AGENT_BIN} serve \
    --queue-dir ${QUEUE_DIR} \
    --state-db ${STATE_DB} \
    --log-dir ${LOG_DIR} \
    --slots ${SLOTS}
Restart=on-failure
RestartSec=10
TimeoutStopSec=86400
StandardOutput=journal
StandardError=journal
KillMode=mixed
KillSignal=SIGTERM

[Install]
WantedBy=default.target
EOF
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now "${AGENT_NAME}"
    sleep 1

    if systemctl --user is-active --quiet "${AGENT_NAME}"; then
        info "Agent is active and running (systemd --user + lingering)"
        info "Agent will survive SSH logout and system reboot"
        return 0
    else
        error "Agent failed to start. Check: journalctl --user -u ${AGENT_NAME} -e"
        systemctl --user status "${AGENT_NAME}" || true
        return 1
    fi
}

# ---- Tier 2: systemd --user (no lingering) ----------------------------------

install_systemd_no_lingering() {
    section "Tier 2: systemd --user (session-scoped)"

    if ! command -v systemctl &>/dev/null; then
        error "systemd not available, skipping Tier 2"
        return 1
    fi

    mkdir -p "$(dirname "$SYSTEMD_UNIT")"
    if [[ -f "$UNIT_SOURCE" ]]; then
        sed "s|%%SLOTS%%|${SLOTS}|g" "$UNIT_SOURCE" > "$SYSTEMD_UNIT"
    else
        cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=ConfFlow Workflow Agent (session-scoped, no lingering)
After=network.target

[Service]
Type=simple
ExecStart=${AGENT_BIN} serve \
    --queue-dir ${QUEUE_DIR} \
    --state-db ${STATE_DB} \
    --log-dir ${LOG_DIR} \
    --slots ${SLOTS}
Restart=on-failure
RestartSec=10
TimeoutStopSec=86400
StandardOutput=journal
StandardError=journal
KillMode=mixed
KillSignal=SIGTERM

[Install]
WantedBy=default.target
EOF
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now "${AGENT_NAME}"
    sleep 1

    if systemctl --user is-active --quiet "${AGENT_NAME}"; then
        warn "Agent is active (systemd --user) but NOT persistent — it will stop when SSH session ends"
        warn "Run 'loginctl enable-linger $AGENT_USER' as root to make it persistent (Tier 1)"
        return 0
    else
        error "Agent failed to start. Check: journalctl --user -u ${AGENT_NAME} -e"
        return 1
    fi
}

# ---- Tier 3: supervisord -----------------------------------------------------

install_supervisord() {
    section "Tier 3: supervisord"

    if ! command -v supervisord &>/dev/null; then
        error "supervisord not available, skipping Tier 3"
        error "Install with: pip install supervisor"
        return 1
    fi

    local CONF_DIR="${AGENT_HOME_DIR}/.config/supervisor"
    local CONF_FILE="${CONF_DIR}/${AGENT_NAME}.conf"

    mkdir -p "$CONF_DIR"
    mkdir -p "$LOG_DIR"

    cat > "$CONF_FILE" <<EOF
[program:${AGENT_NAME}]
command=${AGENT_BIN} serve --queue-dir ${QUEUE_DIR} --state-db ${STATE_DB} --log-dir ${LOG_DIR} --slots ${SLOTS}
directory=${AGENT_HOME_DIR}
autostart=true
autorestart=true
stderr_logfile=${LOG_DIR}/${AGENT_NAME}.err.log
stdout_logfile=${LOG_DIR}/${AGENT_NAME}.out.log
user=${AGENT_USER}
environment=HOME="${AGENT_HOME_DIR}"
EOF

    info "Supervisor config written to $CONF_FILE"

    if supervisorctl reread &>/dev/null && supervisorctl update &>/dev/null; then
        info "Agent registered with supervisord"
    elif supervisorctl -c /etc/supervisor/supervisord.conf reread &>/dev/null; then
        supervisorctl -c /etc/supervisor/supervisord.conf update
    else
        warn "Could not update supervisorctl — make sure supervisord is running"
        warn "Run: supervisord -c $CONF_FILE"
    fi

    sleep 2
    if pgrep -f "${AGENT_BIN} serve" &>/dev/null; then
        info "Agent is running under supervisord"
        return 0
    else
        error "Agent is not running under supervisord. Check: tail $LOG_DIR/*.log"
        return 1
    fi
}

# ---- Tier 4: nohup + setsid --------------------------------------------------

install_nohup() {
    section "Tier 4: nohup + setsid (manual, SSH-session-scoped)"

    if [[ -f "${AGENT_HOME_DIR}/${AGENT_NAME}.pid" ]]; then
        warn "PID file exists. Agent may already be running."
        local pid
        pid=$(cat "${AGENT_HOME_DIR}/${AGENT_NAME}.pid")
        if kill -0 "$pid" 2>/dev/null; then
            error "Agent is already running (PID $pid). Stop it first."
            return 1
        else
            warn "Stale PID file removed"
            rm -f "${AGENT_HOME_DIR}/${AGENT_NAME}.pid"
        fi
    fi

    mkdir -p "$LOG_DIR"

    setsid bash -c "
        nohup ${AGENT_BIN} serve \
            --queue-dir ${QUEUE_DIR} \
            --state-db ${STATE_DB} \
            --log-dir ${LOG_DIR} \
            --slots ${SLOTS} \
            >> ${LOG_DIR}/${AGENT_NAME}.out.log 2>> ${LOG_DIR}/${AGENT_NAME}.err.log &
        echo \$! > '${AGENT_HOME_DIR}/${AGENT_NAME}.pid'
    "

    sleep 2
    local pid
    pid=$(cat "${AGENT_HOME_DIR}/${AGENT_NAME}.pid" 2>/dev/null || echo "")

    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        info "Agent started with PID $pid"
        warn "This is NOT persistent — agent will stop when SSH session ends"
        warn "For persistence, use Tier 1 (systemd + lingering) or Tier 3 (supervisord)"
        return 0
    else
        error "Agent failed to start. Check: tail $LOG_DIR/${AGENT_NAME}.out.log"
        return 1
    fi
}

# ---- Tier 5: Docker (notes only) ---------------------------------------------

info_tier5() {
    section "Tier 5: Docker (Stage 5 / HPC container use)"
    info "Docker deployment is documented for Stage 5."
    info "Basic usage:"
    info "  docker run -d --restart unless-stopped \\"
    info "    -v ~/.confflow-queue:/root/confflow-queue \\"
    info "    -v ~/.local/share/confflow-agent:/root/.local/share/confflow-agent \\"
    info "    -v ~/.local/log/confflow-agent:/root/.local/log/confflow-agent \\"
    info "    confflow-agent serve --queue-dir /root/confflow-queue ..."
    info ""
    info "See docs/AGENT_SETUP.md for full Docker instructions (Stage 5)."
}

# ---- Main installation logic -------------------------------------------------

main() {
    echo ""
    echo "=============================================="
    echo "  ConfFlow Agent Installation"
    echo "  User: $AGENT_USER | Init: $(detect_init)"
    echo "=============================================="
    echo ""

    if [[ ! -x "$AGENT_BIN" ]]; then
        error "confflow-agent binary not found at $AGENT_BIN"
        error "Install confflow with agent extra first:"
        error "  pip install -e '.[agent]'"
        exit 1
    fi

    check_fs_type "$QUEUE_DIR" || {
        warn "NFS detected. Proceeding anyway but performance may be degraded."
    }

    ensure_dirs

    echo ""
    install_systemd_lingering && {
        info "Installation complete (Tier 1: systemd + lingering)"
        echo ""
        info "Quick commands:"
        info "  systemctl --user status ${AGENT_NAME}"
        info "  journalctl --user -u ${AGENT_NAME} -f"
        info "  confflow-agent list --queue-dir ${QUEUE_DIR}"
        exit 0
    }

    echo ""
    install_systemd_no_lingering && {
        warn "Installation complete (Tier 2: systemd session-scoped)"
        exit 0
    }

    echo ""
    install_supervisord && {
        info "Installation complete (Tier 3: supervisord)"
        exit 0
    }

    echo ""
    install_nohup && {
        warn "Installation complete (Tier 4: nohup, NOT persistent)"
        exit 0
    }

    section "Installation failed on all tiers"
    error "The agent could not be started."
    error "Try one of the following manually:"
    error "  1. As root: loginctl enable-linger $AGENT_USER  # then re-run this script"
    error "  2. pip install supervisor && supervisord  # then re-run"
    error "  3. nohup setsid confflow-agent serve ... &  # manual"
    info ""
    info_tier5
    exit 1
}

main "$@"
