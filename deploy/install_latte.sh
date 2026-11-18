#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: sudo bash deploy/install_latte.sh [--broker-host IP_OR_DNS] [--device /dev/ttyUSB0]

Requires Debian/Ubuntu with systemd, udev, and an attached USB-serial AIS device.
The publisher connects to localhost:1883; no server IP is required.
--broker-host optionally sets the subscriber address shown at the end (default: localhost).
Without --device, selects FTDI FT231X USB UART 0403:6015, serial DU0ERIKF.
Scans ttyUSB*/ttyACM* directly; /dev/serial/by-id links are not required.
Use --device to override automatic selection for another adapter or interface.
The installer prompts for ais_serial and ais_reader passwords; it never stores them in Git.
EOF
}

broker_host="localhost"
device=""
while (($#)); do
    case "$1" in
        --broker-host|--device)
            if (($# < 2)); then usage >&2; exit 2; fi
            if [[ "$1" == "--broker-host" ]]; then
                broker_host="$2"
            else
                device="$2"
            fi
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Jalankan dengan sudo di Latte." >&2
    exit 1
fi
if [[ ! "$broker_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
    echo "--broker-host harus IP v4 atau nama DNS Latte yang dapat diakses subscriber." >&2
    exit 1
fi

if [[ "$broker_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    python3 -c 'import ipaddress, sys; ipaddress.IPv4Address(sys.argv[1])' "$broker_host"
fi

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root_dir/deploy/detect_ais_device.sh"

if [[ -z "$device" ]]; then
    shopt -s nullglob
    devices=(/dev/ttyUSB* /dev/ttyACM*)
    shopt -u nullglob
    device="$(detect_ais_device "${devices[@]}")"
    echo "Adapter AIS terdeteksi: $device (FT231X, 0403:6015, serial DU0ERIKF)"
fi
device="$(realpath -e -- "$device")"
if [[ ! "$device" =~ ^/dev/tty(USB|ACM)[0-9]+$ ]]; then
    echo "Perangkat harus USB-serial ttyUSB* atau ttyACM*: $device" >&2
    exit 1
fi
if [[ -e /dev/ais && "$(realpath -e -- /dev/ais)" != "$device" ]]; then
    echo "/dev/ais sudah digunakan oleh perangkat USB-serial lain." >&2
    exit 1
fi

app_dir=/opt/ais-jrc
config_dir=/etc/ais-jrc
broker_dir=/etc/mosquitto/ais-jrc
broker_conf=/etc/mosquitto/conf.d/ais-jrc.conf
passwd_file="$broker_dir/passwd"
acl_file="$broker_dir/acl"

echo "Memasang paket sistem dan dependensi Python..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    mosquitto python3 python3-venv udev

if [[ ! -f /etc/mosquitto/mosquitto.conf ]] ||
        ! grep -Eq '^[[:space:]]*include_dir[[:space:]]+/etc/mosquitto/conf.d[[:space:]]*$' \
        /etc/mosquitto/mosquitto.conf; then
    echo "Mosquitto tidak memuat /etc/mosquitto/conf.d; konfigurasi manual diperlukan." >&2
    exit 1
fi
shopt -s nullglob
other_configs=(/etc/mosquitto/conf.d/*.conf)
shopt -u nullglob
for config in "${other_configs[@]}"; do
    if [[ "$config" != "$broker_conf" ]]; then
        echo "Broker sudah punya konfigurasi lain ($config); tidak diubah otomatis." >&2
        exit 1
    fi
done
if [[ ! -e "$broker_conf" ]] &&
        { [[ -e "$passwd_file" ]] || [[ -e "$acl_file" ]] ||
          [[ -e "$broker_dir/ca.key" ]]; }; then
    echo "Berkas broker sudah ada tanpa konfigurasi installer; periksa manual." >&2
    exit 1
fi

props="$(udevadm info --query=property --name="$device")"
get_prop() {
    printf '%s\n' "$props" | sed -n "s/^$1=//p" | head -n 1
}
vendor="$(get_prop ID_VENDOR_ID)"
product="$(get_prop ID_MODEL_ID)"
serial="$(get_prop ID_SERIAL_SHORT)"
path="$(get_prop ID_PATH)"
if [[ -n "$serial" && ! "$serial" =~ ^[A-Za-z0-9._:-]+$ ]] ||
        [[ -n "$path" && ! "$path" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
    echo "Identitas USB berisi karakter yang tidak aman untuk aturan udev." >&2
    exit 1
fi
if [[ -n "$serial" && -n "$vendor" && -n "$product" ]]; then
    if [[ ! "$vendor" =~ ^[0-9A-Fa-f]{4}$ || ! "$product" =~ ^[0-9A-Fa-f]{4}$ ]]; then
        echo "ID vendor/product USB tidak valid." >&2
        exit 1
    fi
    rule="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"$vendor\", ATTRS{idProduct}==\"$product\", ENV{ID_SERIAL_SHORT}==\"$serial\""
elif [[ -n "$path" ]]; then
    rule="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"$path\""
    echo "Perangkat tidak punya serial unik; /dev/ais mengikuti port USB yang sama."
else
    echo "Tidak ada ID_SERIAL_SHORT atau ID_PATH untuk membuat /dev/ais." >&2
    exit 1
fi

if ! getent group dialout >/dev/null; then
    echo "Grup dialout tidak ditemukan." >&2
    exit 1
fi
if ! getent group ais-jrc >/dev/null; then
    groupadd --system ais-jrc
fi
if ! id -u ais-jrc >/dev/null 2>&1; then
    useradd --system --gid ais-jrc --groups dialout \
        --home-dir "$app_dir" --no-create-home --shell /usr/sbin/nologin ais-jrc
else
    usermod -aG dialout ais-jrc
fi

install -d -m 0755 -o root -g root "$app_dir"
install -m 0644 "$root_dir/decode.py" "$root_dir/mqtt_publisher.py" \
    "$root_dir/serial_connection.py" \
    "$root_dir/requirements.txt" "$root_dir/README.md" "$app_dir/"
umask 022
if [[ ! -e "$app_dir/.venv/bin/python" ]]; then
    python3 -m venv "$app_dir/.venv"
fi
"$app_dir/.venv/bin/python" -m pip install -r "$app_dir/requirements.txt"
umask 077

install -d -m 0750 -o root -g ais-jrc "$config_dir"
install -d -m 0750 -o root -g mosquitto "$broker_dir"

read -r -s -p "Password broker untuk ais_serial: " mqtt_password
printf '\n'
if [[ -z "$mqtt_password" || "$mqtt_password" == *"'"* ||
        "$mqtt_password" == *'\'* || "$mqtt_password" == *$'\n'* ||
        "$mqtt_password" == *$'\r'* ]]; then
    echo "Password kosong atau mengandung karakter yang tidak didukung di EnvironmentFile." >&2
    exit 1
fi
read -r -s -p "Password subscriber untuk ais_reader: " reader_password
printf '\n'
if [[ -z "$reader_password" ]]; then
    echo "Password ais_reader tidak boleh kosong." >&2
    exit 1
fi
if [[ -e "$passwd_file" ]]; then
    printf '%s\n%s\n' "$mqtt_password" "$mqtt_password" |
        mosquitto_passwd "$passwd_file" ais_serial >/dev/null
else
    printf '%s\n%s\n' "$mqtt_password" "$mqtt_password" |
        mosquitto_passwd -c "$passwd_file" ais_serial >/dev/null
fi
printf '%s\n%s\n' "$reader_password" "$reader_password" |
    mosquitto_passwd "$passwd_file" ais_reader >/dev/null
unset reader_password
chown root:mosquitto "$passwd_file"
chmod 0640 "$passwd_file"

if [[ ! -e "$acl_file" ]]; then
    cat > "$acl_file" <<'EOF'
user ais_serial
topic write ais/aivdm/raw
topic write ais/aivdo/position
topic write ais/aivdm/decoded
EOF
fi
if ! grep -Fxq "user ais_reader" "$acl_file"; then
    printf '\nuser ais_reader\ntopic read ais/aivdm/raw\ntopic read ais/aivdo/position\ntopic read ais/aivdm/decoded\n' \
        >> "$acl_file"
fi
chown root:mosquitto "$acl_file"
chmod 0640 "$acl_file"

cat > "$broker_conf" <<EOF
listener 1883 0.0.0.0
allow_anonymous false
password_file $passwd_file
acl_file $acl_file
EOF
chmod 0644 "$broker_conf"

env_tmp="$(mktemp "$config_dir/ais.env.XXXXXX")"
trap 'rm -f -- "$env_tmp"' EXIT
printf "MQTT_HOST=localhost\nMQTT_PORT=1883\nMQTT_TLS=0\nMQTT_USERNAME=ais_serial\nMQTT_PASSWORD='%s'\n" \
    "$mqtt_password" > "$env_tmp"
install -m 0640 -o root -g ais-jrc "$env_tmp" "$config_dir/ais.env"
rm -f -- "$env_tmp"
trap - EXIT
unset mqtt_password

printf '%s, SYMLINK+="ais", GROUP="dialout", MODE="0660"\n' "$rule" \
    > /etc/udev/rules.d/99-ais-jrc.rules
chmod 0644 /etc/udev/rules.d/99-ais-jrc.rules
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=tty
udevadm settle
if [[ ! -e /dev/ais ]]; then
    echo "Periksa aturan udev: /dev/ais belum muncul untuk $device." >&2
    exit 1
fi

install -m 0644 "$root_dir/deploy/ais-jrc.service" /etc/systemd/system/ais-jrc.service
systemctl daemon-reload
systemctl enable --now mosquitto.service
systemctl restart mosquitto.service
systemctl enable --now ais-jrc.service
systemctl restart ais-jrc.service
echo "Selesai: /dev/ais -> $device; publisher MQTT: localhost:1883"
if [[ "$broker_host" == "localhost" || "$broker_host" == "127.0.0.1" ]]; then
    echo "Subscriber di Latte: localhost:1883. Dari komputer lain, gunakan IP atau hostname Latte."
else
    echo "Subscriber dari komputer lain: $broker_host:1883"
fi
echo "Tanpa TLS, listener di semua antarmuka: batasi port 1883 ke LAN tepercaya."
echo "Status: systemctl status ais-jrc mosquitto"
