# /etc/profile.d/nomad-banner.sh
#
# Dynamic welcome banner for the Nomad USB. Runs on every interactive
# login shell (both console tty and SSH), so the IP shown is always
# current — no stale MOTD cache, no pam_motd dance, no scripts that
# only run on cron.
#
# Guarded to only display for interactive shells so it doesn't spam
# scp/sftp/git-over-ssh sessions.

case "$-" in
    *i*)
        # Interactive — show the banner
        ;;
    *)
        # Non-interactive shell — only show if explicitly forced
        # (used by `nomad motd` to re-display from any context).
        if [ -z "${NOMAD_BANNER_FORCE:-}" ]; then
            return 0 2>/dev/null || exit 0
        fi
        ;;
esac

# Only show once per shell — subshells (e.g. sudo -i) shouldn't re-display.
# Set NOMAD_BANNER_FORCE=1 before sourcing to bypass this — used by the
# `nomad motd` subcommand so users can re-display on demand.
if [ -n "${NOMAD_BANNER_SHOWN:-}" ] && [ -z "${NOMAD_BANNER_FORCE:-}" ]; then
    return 0 2>/dev/null || exit 0
fi
export NOMAD_BANNER_SHOWN=1

# Find the primary LAN IPs. Returns one or more lines, one IP per
# line, suitable for embedding in the URL templates below.
#
# Strategy: list every IPv4 address bound to a real-looking interface
# (skip loopback, skip docker bridges, skip virtual veth pairs).
# That gives us the ethernet IP AND the hotspot IP if both are up,
# which is the common case for the survival-USB use:
#   - You're hosting Nomad over an ethernet you also brought up
#   - You're ALSO running a hotspot for clients
# Each interface gets its own URL line so users on either network
# can see the right address.
_nomad_ips() {
    ip -4 -brief addr show 2>/dev/null \
        | awk '!/^lo|^docker|^br-|^veth/ && $3 ~ /\./ {
            split($3, a, "/"); print a[1]
        }'
}

_IPS=$(_nomad_ips)

# Read the ISO version (baked in by build.sh from the repo's VERSION
# file). Falls back to "unknown" if the file's missing — shouldn't
# happen on a properly-built ISO but safer than printing nothing.
_NOMAD_VERSION="unknown"
if [ -r /etc/nomad-version ]; then
    _NOMAD_VERSION=$(tr -d '[:space:]' < /etc/nomad-version)
fi

# Read the current Nomad phase from /run/nomad-status if it exists.
# This is written by nomad-init and nomad-start so users who SSH in
# during first-boot initialization see the progress instead of a
# silent MOTD that makes it look like Nomad is hung.
_nomad_phase() {
    if [ -r /run/nomad-status ]; then
        local p
        p=$(cat /run/nomad-status 2>/dev/null)
        case "$p" in
            ready)  printf 'ready' ;;
            failed) printf 'FAILED (check journalctl -u nomad-start)' ;;
            '')     printf 'starting' ;;
            *)      printf '%s' "$p" ;;
        esac
    else
        printf 'starting'
    fi
}

_PHASE=$(_nomad_phase)

# Build the URL block. If we have multiple IPs (e.g. ethernet AND
# hotspot active), print one line per IP so users on either network
# know how to reach Nomad. If we have none, say so explicitly rather
# than printing a broken-looking "http://:8080".
_url_lines() {
    local kind="$1" port="$2"
    if [ -z "$_IPS" ]; then
        printf '  %-13s (no network)\n' "${kind}:"
        return
    fi
    local first=1
    while IFS= read -r ip; do
        if [ "$first" = "1" ]; then
            printf '  %-13s http://%s:%s\n' "${kind}:" "$ip" "$port"
            first=0
        else
            printf '  %-13s http://%s:%s\n' "" "$ip" "$port"
        fi
    done <<< "$_IPS"
}

cat <<EOF

  ╔═══════════════════════════════════════════════╗
  ║            PROJECT NOMAD USB                  ║
  ║         Offline Survival Server               ║
  ╚═══════════════════════════════════════════════╝
                              v${_NOMAD_VERSION}

  Default user: nomad  /  password: nomad
  Root password: nomad        (change these!)

  Status:       ${_PHASE}
EOF

_url_lines "Nomad admin" 8080
_url_lines "Dozzle logs" 9999

cat <<EOF

  Data partition: /mnt/nomad-data

  Useful commands:
    nomad help                         # Full command reference
    nomad status                       # Current phase + containers
    nomad watch                        # Live-updating status view
    nomad journal                      # Live systemd journal
    nomad motd                         # Re-show this banner
    nomad tips                         # Tips for common issues
    sudo nomad-hotspot on              # Start WiFi hotspot

EOF

unset _IPS _PHASE _NOMAD_VERSION
unset -f _nomad_ips _nomad_phase _url_lines
