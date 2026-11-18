#!/usr/bin/env bash
# Sourced by install_latte.sh. Inspect actual tty devices without relying on by-id links.

detect_ais_device() {
    local candidate properties key value vendor product serial
    local matches=()

    for candidate in "$@"; do
        [[ "$candidate" =~ ^/dev/tty(USB|ACM)[0-9]+$ ]] || continue
        if ! properties="$(udevadm info --query=property --name="$candidate")"; then
            echo "Tidak dapat membaca identitas USB: $candidate" >&2
            continue
        fi
        vendor=""
        product=""
        serial=""
        while IFS='=' read -r key value; do
            case "$key" in
                ID_VENDOR_ID) vendor="$value" ;;
                ID_MODEL_ID) product="$value" ;;
                ID_SERIAL_SHORT) serial="$value" ;;
            esac
        done <<< "$properties"

        # This installation's FTDI FT231X AIS adapter, identified from its USB descriptors.
        if [[ "$vendor" == "0403" && "$product" == "6015" && "$serial" == "DU0ERIKF" ]]; then
            matches+=("$candidate")
        fi
    done

    case "${#matches[@]}" in
        1) printf '%s\n' "${matches[0]}" ;;
        0)
            echo "Adapter AIS FT231X (0403:6015, serial DU0ERIKF) belum ditemukan." >&2
            echo "Pasang adapter tersebut lalu jalankan ulang installer; untuk adapter lain gunakan --device." >&2
            return 1
            ;;
        *)
            echo "Lebih dari satu port cocok dengan adapter AIS: ${matches[*]}" >&2
            echo "Gunakan --device untuk memilih port AIS yang benar." >&2
            return 1
            ;;
    esac
}
