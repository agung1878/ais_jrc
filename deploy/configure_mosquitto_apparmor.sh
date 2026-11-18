#!/usr/bin/env bash
# Allow Mosquitto to read only the AIS password and ACL files through a local override.

configure_mosquitto_apparmor() {
    local profile_dir="${1:-/etc/apparmor.d}"
    local profile_name profile local_name local_file line rule
    local include_pattern='^[[:space:]]*#?include([[:space:]]+if[[:space:]]+exists)?[[:space:]]+<local/(mosquitto|usr\.sbin\.mosquitto)>'

    # Do not enable AppArmor or profiles the administrator has disabled.
    if [[ -f /sys/module/apparmor/parameters/enabled ]] &&
            [[ "$(cat /sys/module/apparmor/parameters/enabled)" != "Y" ]]; then
        return 0
    fi

    for profile_name in mosquitto usr.sbin.mosquitto; do
        profile="$profile_dir/$profile_name"
        [[ -f "$profile" ]] || continue
        [[ ! -e "$profile_dir/disable/$profile_name" && ! -L "$profile_dir/disable/$profile_name" ]] || continue
        if ! command -v apparmor_parser >/dev/null 2>&1; then
            echo "Profil Mosquitto ditemukan tetapi apparmor_parser tidak tersedia." >&2
            return 1
        fi

        local_name=""
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" =~ $include_pattern ]]; then
                local_name="${BASH_REMATCH[2]}"
                break
            fi
        done < "$profile"
        if [[ -z "$local_name" ]]; then
            echo "Profil $profile belum memuat local override Mosquitto." >&2
            echo "Tambahkan include if exists <local/$profile_name> di dalam profil lalu jalankan ulang skrip ini." >&2
            return 1
        fi

        install -d -m 0755 "$profile_dir/local"
        local_file="$profile_dir/local/$local_name"
        if [[ ! -e "$local_file" ]]; then
            install -m 0644 /dev/null "$local_file"
        fi
        for rule in '/etc/mosquitto/ais-jrc/passwd r,' '/etc/mosquitto/ais-jrc/acl r,'; do
            if ! grep -Fxq "$rule" "$local_file"; then
                printf '\n%s\n' "$rule" >> "$local_file"
            fi
        done
        if ! apparmor_parser -r "$profile"; then
            echo "Gagal memuat ulang profil AppArmor $profile; Mosquitto belum direstart." >&2
            return 1
        fi
        echo "AppArmor Mosquitto: akses baca password/ACL AIS diizinkan melalui $local_file"
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    if [[ $EUID -ne 0 ]]; then
        echo "Jalankan dengan sudo di Ubuntu." >&2
        exit 1
    fi
    configure_mosquitto_apparmor
fi
