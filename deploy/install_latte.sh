#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: sudo bash deploy/install_latte.sh [OPTIONS]

Options:
  --components LIST   Comma-separated broker,service,device (default: all)
  --device PATH       AIS port, e.g. /dev/ttyUSB1 (requires device component)
  --broker-host HOST  Address shown to subscribers (requires broker component)
  -h, --help          Show this help

Examples:
  sudo bash deploy/install_latte.sh --components broker,service
  sudo bash deploy/install_latte.sh --components device --device /dev/ttyUSB1
  sudo bash deploy/install_latte.sh --components all

broker: Mosquitto, MQTT accounts/ACL, AppArmor.
service: Python dependencies and systemd reader; can wait for USB.
device: creates /dev/ais from the attached AIS USB serial identity.
Without --device, selects FT231X 0403:6015 serial DU0ERIKF.
The publisher always connects to localhost:1883. Passwords are requested
only when installing a new broker, not for service or device alone.
EOF
}

components=all
broker_host=localhost
device=""
device_given=false
broker_host_given=false
while (($#)); do
    case "$1" in
        --components|--broker-host|--device)
            if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
                echo "Nilai untuk $1 wajib diisi." >&2
                usage >&2
                exit 2
            fi
            case "$1" in
                --components) components="$2" ;;
                --broker-host) broker_host="$2"; broker_host_given=true ;;
                --device) device="$2"; device_given=true ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Argumen tidak dikenal: $1" >&2; usage >&2; exit 2 ;;
    esac
done

do_broker=false
do_service=false
do_device=false
if [[ "$components" == all ]]; then
    do_broker=true; do_service=true; do_device=true
else
    if [[ "$components" == ,* || "$components" == *, || "$components" == *,,* ]]; then
        echo "--components tidak boleh berisi komponen kosong." >&2
        exit 2
    fi
    IFS=, read -r -a selected <<< "$components"
    for component in "${selected[@]}"; do
        case "$component" in
            broker) do_broker=true ;;
            service) do_service=true ;;
            device) do_device=true ;;
            *) echo "Komponen tidak dikenal: $component" >&2; exit 2 ;;
        esac
    done
fi
if [[ "$device_given" == true && "$do_device" != true ]]; then
    echo "--device hanya untuk komponen device." >&2; exit 2
fi
if [[ "$broker_host_given" == true && "$do_broker" != true ]]; then
    echo "--broker-host hanya untuk komponen broker." >&2; exit 2
fi
if [[ $EUID -ne 0 ]]; then
    echo "Jalankan dengan sudo di Latte." >&2; exit 1
fi
if [[ ! "$broker_host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
    echo "--broker-host harus IP v4 atau nama DNS yang dapat diakses subscriber." >&2
    exit 1
fi
if [[ "$broker_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    IFS=. read -r -a octets <<< "$broker_host"
    for octet in "${octets[@]}"; do
        if ((${#octet} > 3 || 10#$octet > 255)); then
            echo "--broker-host bukan alamat IPv4 valid: $broker_host" >&2
            exit 1
        fi
    done
fi

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
config_dir=/etc/ais-jrc
broker_dir=/etc/mosquitto/ais-jrc
broker_conf=/etc/mosquitto/conf.d/ais-jrc.conf
passwd_file="$broker_dir/passwd"
acl_file="$broker_dir/acl"
env_file="$config_dir/ais.env"

if [[ "$do_service" == true && "$do_broker" != true ]] &&
        { [[ ! -f "$broker_conf" ]] || [[ ! -f "$passwd_file" ]] || [[ ! -f "$env_file" ]]; }; then
    echo "Komponen service membutuhkan broker AIS yang sudah terpasang; pilih broker,service." >&2
    exit 1
fi

# Validate the adapter before making any system changes. Other components never inspect USB.
if [[ "$do_device" == true ]]; then
    source "$root_dir/deploy/detect_ais_device.sh"
    if ! command -v udevadm >/dev/null 2>&1; then
        echo "udevadm belum tersedia; pasang paket udev terlebih dahulu." >&2; exit 1
    fi
    if [[ -z "$device" ]]; then
        shopt -s nullglob
        devices=(/dev/ttyUSB* /dev/ttyACM*)
        shopt -u nullglob
        device="$(detect_ais_device "${devices[@]}")"
        echo "Adapter AIS terdeteksi: $device (FT231X, 0403:6015, serial DU0ERIKF)"
    fi
    device="$(realpath -e -- "$device")"
    if [[ ! "$device" =~ ^/dev/tty(USB|ACM)[0-9]+$ ]]; then
        echo "Perangkat harus USB-serial ttyUSB* atau ttyACM*: $device" >&2; exit 1
    fi
    if [[ -e /dev/ais && "$(realpath -e -- /dev/ais)" != "$device" ]]; then
        echo "/dev/ais sudah digunakan oleh perangkat USB-serial lain." >&2; exit 1
    fi
    props="$(udevadm info --query=property --name="$device")"
    get_prop() { printf '%s\n' "$props" | sed -n "s/^$1=//p" | head -n 1; }
    vendor="$(get_prop ID_VENDOR_ID)"
    product="$(get_prop ID_MODEL_ID)"
    serial="$(get_prop ID_SERIAL_SHORT)"
    path="$(get_prop ID_PATH)"
    if [[ -n "$serial" && ! "$serial" =~ ^[A-Za-z0-9._:-]+$ ]] ||
            [[ -n "$path" && ! "$path" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
        echo "Identitas USB berisi karakter yang tidak aman untuk aturan udev." >&2; exit 1
    fi
    if [[ -n "$serial" && -n "$vendor" && -n "$product" ]]; then
        if [[ ! "$vendor" =~ ^[0-9A-Fa-f]{4}$ || ! "$product" =~ ^[0-9A-Fa-f]{4}$ ]]; then
            echo "ID vendor/product USB tidak valid." >&2; exit 1
        fi
        rule="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"$vendor\", ATTRS{idProduct}==\"$product\", ENV{ID_SERIAL_SHORT}==\"$serial\""
    elif [[ -n "$path" ]]; then
        rule="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"$path\""
        echo "Perangkat tidak punya serial unik; /dev/ais mengikuti port USB yang sama."
    else
        echo "Tidak ada ID_SERIAL_SHORT atau ID_PATH untuk membuat /dev/ais." >&2; exit 1
    fi
fi

packages=()
if [[ "$do_broker" == true ]]; then packages+=(mosquitto python3); fi
if [[ "$do_service" == true ]]; then packages+=(acl python3 python3-venv); fi
if ((${#packages[@]})); then
    echo "Memasang paket untuk komponen: $components"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
fi

ensure_service_user() {
    if ! getent group dialout >/dev/null; then
        echo "Grup dialout tidak ditemukan." >&2; return 1
    fi
    if ! getent group ais-jrc >/dev/null; then groupadd --system ais-jrc; fi
    if ! id -u ais-jrc >/dev/null 2>&1; then
        useradd --system --gid ais-jrc --groups dialout \
            --home-dir "$root_dir" --no-create-home --shell /usr/sbin/nologin ais-jrc
    else
        usermod -aG dialout ais-jrc
    fi
}
if [[ "$do_broker" == true || "$do_service" == true ]]; then ensure_service_user; fi

if [[ "$do_broker" == true ]]; then
    source "$root_dir/deploy/configure_mosquitto_apparmor.sh"
    if [[ ! -f /etc/mosquitto/mosquitto.conf ]] ||
            ! grep -Eq '^[[:space:]]*include_dir[[:space:]]+/etc/mosquitto/conf.d[[:space:]]*$' /etc/mosquitto/mosquitto.conf; then
        echo "Mosquitto tidak memuat /etc/mosquitto/conf.d; konfigurasi manual diperlukan." >&2; exit 1
    fi
    shopt -s nullglob
    other_configs=(/etc/mosquitto/conf.d/*.conf)
    shopt -u nullglob
    for config in "${other_configs[@]}"; do
        if [[ "$config" != "$broker_conf" ]]; then
            echo "Broker sudah punya konfigurasi lain ($config); tidak diubah otomatis." >&2; exit 1
        fi
    done
    if [[ -e "$broker_conf" ]]; then
        if [[ ! -f "$passwd_file" || ! -f "$acl_file" || ! -f "$env_file" ]]; then
            echo "Konfigurasi broker AIS tidak lengkap; periksa $broker_dir dan $env_file." >&2; exit 1
        fi
        echo "Broker AIS sudah terpasang; password lama dipertahankan."
    else
        if [[ -e "$passwd_file" || -e "$acl_file" || -e "$env_file" || -e "$broker_dir/ca.key" ]]; then
            echo "Berkas broker sudah ada tanpa konfigurasi installer; periksa manual." >&2; exit 1
        fi
        install -d -m 0750 -o root -g ais-jrc "$config_dir"
        install -d -m 0750 -o root -g mosquitto "$broker_dir"
        read -r -s -p "Password broker untuk ais_serial: " mqtt_password; printf '\n'
        if [[ -z "$mqtt_password" || "$mqtt_password" == *"'"* ||
                "$mqtt_password" == *'\'* || "$mqtt_password" == *$'\n'* ||
                "$mqtt_password" == *$'\r'* ]]; then
            echo "Password kosong atau mengandung karakter yang tidak didukung di EnvironmentFile." >&2; exit 1
        fi
        read -r -s -p "Password subscriber untuk ais_reader: " reader_password; printf '\n'
        if [[ -z "$reader_password" ]]; then
            echo "Password ais_reader tidak boleh kosong." >&2; exit 1
        fi
        printf '%s\n%s\n' "$mqtt_password" "$mqtt_password" |
            mosquitto_passwd -c "$passwd_file" ais_serial >/dev/null
        printf '%s\n%s\n' "$reader_password" "$reader_password" |
            mosquitto_passwd "$passwd_file" ais_reader >/dev/null
        unset reader_password
        chown root:mosquitto "$passwd_file"; chmod 0640 "$passwd_file"
        cat > "$acl_file" <<'EOF'
user ais_serial
topic write ais/aivdm/raw
topic write ais/aivdo/position
topic write ais/aivdm/decoded

user ais_reader
topic read ais/aivdm/raw
topic read ais/aivdo/position
topic read ais/aivdm/decoded
EOF
        chown root:mosquitto "$acl_file"; chmod 0640 "$acl_file"
        cat > "$broker_conf" <<EOF
listener 1883 0.0.0.0
allow_anonymous false
password_file $passwd_file
acl_file $acl_file
log_dest stderr
EOF
        chmod 0644 "$broker_conf"
        env_tmp="$(mktemp "$config_dir/ais.env.XXXXXX")"
        trap 'rm -f -- "$env_tmp"' EXIT
        printf "MQTT_HOST=localhost\nMQTT_PORT=1883\nMQTT_TLS=0\nMQTT_USERNAME=ais_serial\nMQTT_PASSWORD='%s'\n" \
            "$mqtt_password" > "$env_tmp"
        install -m 0640 -o root -g ais-jrc "$env_tmp" "$env_file"
        rm -f -- "$env_tmp"; trap - EXIT; unset mqtt_password
    fi
    configure_mosquitto_apparmor
    systemctl enable mosquitto.service
    systemctl reset-failed mosquitto.service
    if ! systemctl restart mosquitto.service; then
        echo "Mosquitto gagal dimulai. Log broker:" >&2
        journalctl -u mosquitto.service -n 60 --no-pager >&2 || true
        exit 1
    fi
fi

if [[ "$do_device" == true ]]; then
    if ! getent group dialout >/dev/null; then
        echo "Grup dialout tidak ditemukan." >&2; exit 1
    fi
    printf '%s, SYMLINK+="ais", GROUP="dialout", MODE="0660"\n' "$rule" > /etc/udev/rules.d/99-ais-jrc.rules
    chmod 0644 /etc/udev/rules.d/99-ais-jrc.rules
    udevadm control --reload-rules
    udevadm trigger --action=add --subsystem-match=tty
    udevadm settle
    if [[ ! -e /dev/ais || "$(realpath -e -- /dev/ais)" != "$device" ]]; then
        echo "Periksa aturan udev: /dev/ais belum mengarah ke $device." >&2; exit 1
    fi
    echo "Perangkat AIS: /dev/ais -> $device"
fi

if [[ "$do_service" == true ]]; then
    if [[ ! -f "$broker_conf" || ! -f "$passwd_file" || ! -f "$env_file" ]]; then
        echo "Komponen service membutuhkan broker AIS yang sudah terpasang." >&2; exit 1
    fi
    bash "$root_dir/deploy/activate_repo_service.sh" --install-only
    systemctl enable ais-jrc.service
    systemctl restart ais-jrc.service
    sleep 2
    if ! systemctl is-active --quiet ais-jrc.service; then
        echo "Service AIS berhenti setelah dimulai; periksa journalctl -u ais-jrc.service." >&2
        exit 1
    fi
    if [[ "$do_device" != true && ! -e /dev/ais ]]; then
        echo "Service aktif dan menunggu /dev/ais; konfigurasi device dapat dilakukan nanti."
    fi
fi

if [[ "$do_broker" == true ]]; then
    echo "Publisher MQTT: localhost:1883"
    if [[ "$broker_host" == localhost || "$broker_host" == 127.0.0.1 ]]; then
        echo "Subscriber lokal: localhost:1883. Dari komputer lain, gunakan IP server."
    else
        echo "Subscriber dari komputer lain: $broker_host:1883"
    fi
    echo "Tanpa TLS, listener di semua antarmuka: batasi port 1883 ke LAN tepercaya."
fi
echo "Instalasi komponen $components selesai."
