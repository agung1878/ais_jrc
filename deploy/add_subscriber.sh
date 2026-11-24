#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 1 || ! "$1" =~ ^[A-Za-z][A-Za-z0-9._-]*$ || "$1" == "ais_serial" ]]; then
    echo "Usage: sudo bash deploy/add_subscriber.sh USERNAME (selain ais_serial)" >&2
    exit 2
fi
if [[ $EUID -ne 0 ]]; then
    echo "Jalankan dengan sudo di Latte." >&2
    exit 1
fi

passwd_file=/etc/mosquitto/ais-jrc/passwd
acl_file=/etc/mosquitto/ais-jrc/acl
if [[ ! -f "$passwd_file" || ! -f "$acl_file" ]]; then
    echo "Jalankan install_latte.sh terlebih dahulu." >&2
    exit 1
fi

username="$1"
read -r -s -p "Password subscriber $username: " password
printf '\n'
if [[ -z "$password" ]]; then
    echo "Password tidak boleh kosong." >&2
    exit 1
fi
printf '%s\n%s\n' "$password" "$password" |
    mosquitto_passwd "$passwd_file" "$username" >/dev/null
unset password
chown mosquitto:mosquitto "$passwd_file"
chmod 0640 "$passwd_file"

if ! grep -Fxq "user $username" "$acl_file"; then
    printf '\nuser %s\ntopic read ais/aivdm/raw\ntopic read ais/aivdo/position\ntopic read ais/aivdm/decoded\n' \
        "$username" >> "$acl_file"
fi
chown mosquitto:mosquitto "$acl_file"
chmod 0640 "$acl_file"
systemctl restart mosquitto.service
echo "Akun subscriber $username dapat membaca topik AIS (tidak dapat publish)."
