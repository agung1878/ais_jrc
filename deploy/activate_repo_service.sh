#!/usr/bin/env bash
set -euo pipefail

# Install/update the reader service in place; broker credentials stay untouched.
if [[ $EUID -ne 0 ]]; then
    echo "Jalankan dengan sudo dari repo AIS." >&2
    exit 1
fi
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --install-only ) ]]; then
    echo "Usage: sudo bash deploy/activate_repo_service.sh [--install-only]" >&2
    exit 2
fi

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
repo_owner="$(stat -c %U -- "$repo_dir")"
if [[ "$repo_dir" == *$'\n'* || "$repo_dir" == *%* ]]; then
    echo "Path repo tidak boleh berisi newline atau karakter %." >&2
    exit 1
fi
if ! id -u ais-jrc >/dev/null 2>&1; then
    echo "Akun ais-jrc belum ada; jalankan installer lengkap terlebih dahulu." >&2
    exit 1
fi
if [[ ! -f /etc/ais-jrc/ais.env ]]; then
    echo "Konfigurasi MQTT belum ada di /etc/ais-jrc/ais.env." >&2
    exit 1
fi
if [[ ${1:-} != --install-only && ! -f /etc/systemd/system/ais-jrc.service ]]; then
    echo "Unit ais-jrc.service belum terpasang; jalankan installer lengkap." >&2
    exit 1
fi

if ! command -v setfacl >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y acl
fi

umask 022
if [[ ! -x "$repo_dir/.venv/bin/python" ]]; then
    runuser -u "$repo_owner" -- python3 -m venv "$repo_dir/.venv"
fi
runuser -u "$repo_owner" -- "$repo_dir/.venv/bin/python" -m pip install -r "$repo_dir/requirements.txt"

# Only the repository needs read access; ancestors need traversal alone.
setfacl -m u:ais-jrc:rx,d:u:ais-jrc:rx -- "$repo_dir"
setfacl -m u:ais-jrc:r-- -- "$repo_dir"/*.py
setfacl -R -m u:ais-jrc:rX -- "$repo_dir/.venv"
find "$repo_dir/.venv" -type d -exec setfacl -m d:u:ais-jrc:rx -- {} +
parent="$(dirname -- "$repo_dir")"
while [[ "$parent" != / ]]; do
    setfacl -m u:ais-jrc:x -- "$parent"
    parent="$(dirname -- "$parent")"
done

(cd "$repo_dir" && runuser -u ais-jrc -- "$repo_dir/.venv/bin/python" -c \
    'import ais_format, decode, mqtt_publisher')

unit_tmp="$(mktemp /etc/systemd/system/ais-jrc.service.XXXXXX)"
backup_tmp="$(mktemp /etc/systemd/system/ais-jrc.backup.XXXXXX)"
trap 'rm -f -- "$unit_tmp" "$backup_tmp"' EXIT
if [[ -f /etc/systemd/system/ais-jrc.service ]]; then
    cp /etc/systemd/system/ais-jrc.service "$backup_tmp"
fi
python3 - "$repo_dir/deploy/ais-jrc.service" "$repo_dir" > "$unit_tmp" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text()
print(template.replace("@APP_DIR@", sys.argv[2]), end="")
PY
install -m 0644 "$unit_tmp" /etc/systemd/system/ais-jrc.service
rm -f -- "$unit_tmp"
systemctl daemon-reload

if [[ ${1:-} != --install-only ]]; then
    if ! systemctl restart ais-jrc.service; then
        install -m 0644 "$backup_tmp" /etc/systemd/system/ais-jrc.service
        systemctl daemon-reload
        systemctl restart ais-jrc.service || true
        echo "Service baru gagal dimulai; unit lama dipulihkan." >&2
        exit 1
    fi
    sleep 2
    if ! systemctl is-active --quiet ais-jrc.service; then
        install -m 0644 "$backup_tmp" /etc/systemd/system/ais-jrc.service
        systemctl daemon-reload
        systemctl restart ais-jrc.service || true
        echo "Service baru berhenti; unit lama dipulihkan." >&2
        exit 1
    fi
    echo "Service AIS berjalan langsung dari $repo_dir"
fi
