# AIS JRC Serial ke MQTT

Program ini membaca kalimat `!AIVDM` dan `!AIVDO` dari receiver AIS JRC melalui
USB-serial, menampilkan hasil decode, lalu menerbitkannya ke broker MQTT di
Latte. Broker, service pembaca, dan aturan nama perangkat dipasang otomatis di
Latte; tidak ada sertifikat TLS yang perlu diatur untuk pemakaian LAN.

## Hasil yang dipasang di Latte

- `ais-jrc.service` berjalan otomatis saat boot dan membaca `/dev/ais`.
- Mosquitto aktif di port `1883`, menolak klien anonim, dan memakai ACL.
- Service publisher terhubung ke `localhost:1883`; tidak perlu menentukan IP broker.
- `ais_serial` hanya boleh **menulis** data AIS.
- `ais_reader` hanya boleh **membaca** data AIS; dipakai untuk MQTT Explorer
  atau aplikasi penerima.
- `/dev/ais` adalah nama tetap untuk USB-serial AIS dengan izin
  `dialout:0660`.
- Program dipasang di `/opt/ais-jrc`; konfigurasi service tersimpan terbatas di
  `/etc/ais-jrc/ais.env`.

## Topik MQTT

| Topik | Isi |
| --- | --- |
| `ais/aivdm/raw` | Satu kalimat NMEA `!AIVDM,...` apa adanya per fragmen |
| `ais/aivdo/position` | JSON `{"received_at":"...","mmsi":123456789,"lat":-6.2,"lon":106.8}` dari AIVDO dengan posisi valid |
| `ais/aivdm/decoded` | JSON hasil decode AIVDM lengkap |

Semua publish memakai QoS 1, tanpa retained message. Subscriber hanya menerima
pesan yang dikirim setelah ia tersambung; broker tidak menyimpan riwayat.
Posisi AIVDO hanya dipublish jika `lat` dan `lon` valid.

## Persyaratan Latte

- Debian/Ubuntu dengan `systemd`, `apt`, dan `udev`.
- Hak `sudo` dan koneksi jaringan untuk `apt` serta `pip` saat instalasi awal.
- USB-serial AIS sudah terpasang.
- Untuk subscriber dari komputer lain, IP atau hostname Latte harus bisa diakses
  komputer penerima; IP statik atau reservasi DHCP memudahkan koneksi tetap.
- Jika Mosquitto sudah punya konfigurasi lain di `/etc/mosquitto/conf.d`,
  installer berhenti agar tidak menimpa konfigurasi yang ada.

## Instalasi sampai jalan di Latte

### 1. Pasang perangkat dan cek port

```sh
ls -l /dev/serial/by-id/
```

Jika tidak ada entri di `/dev/serial/by-id`, installer tetap dapat mendeteksi
adapter langsung dari `/dev/ttyUSB*` atau `/dev/ttyACM*` melalui identitas udev.

### 2. Ambil kode

```sh
git clone https://github.com/ArifMunawarr/ais_jrc.git
cd ais_jrc
```

### 3. Jalankan installer

Tidak perlu memasukkan port serial atau IP broker. Installer otomatis memilih
adapter AIS **FTDI FT231X USB UART**, vendor `0403`, product `6015`, serial
`DU0ERIKF`, walaupun ada adapter lain atau symlink `/dev/serial/by-id` tidak
tersedia. Nama port boleh berubah dari `ttyUSB0` ke `ttyUSB1`; pemilihan
menggunakan identitas USB. Service terhubung ke Mosquitto lokal:

```sh
sudo bash deploy/install_latte.sh
```

Adapter harus terpasang saat instalasi. Jika adapter yang cocok belum ditemukan,
installer berhenti sebelum memasang paket dan meminta pemasangan adapter.
Untuk adapter lain atau jika lebih dari satu port memiliki identitas yang sama,
pilih perangkat AIS secara eksplisit:

```sh
sudo bash deploy/install_latte.sh --device /dev/ttyUSB0
```

Opsi `--broker-host 192.168.1.50` tetap tersedia jika ingin menampilkan alamat
untuk subscriber dari komputer lain di akhir instalasi. Opsi ini hanya mengatur
petunjuk alamat subscriber; service selalu memakai `localhost:1883`.

Installer akan meminta dua password di terminal; ketiknya lalu tekan Enter.
Karakternya tidak akan tampil:

1. `Password broker untuk ais_serial`
2. `Password subscriber untuk ais_reader`

Masukkan password yang sudah disepakati masing-masing. Password tidak perlu
ditulis ke perintah, kode, atau README. Installer hanya menyimpan password
publisher di `/etc/ais-jrc/ais.env` untuk service dan menyimpan hash password
broker di Mosquitto.

### 4. Verifikasi instalasi

```sh
ls -l /dev/ais
systemctl status mosquitto ais-jrc
ss -lptn | grep 1883
journalctl -u ais-jrc -f
```

Hasil yang diharapkan:

- `/dev/ais` mengarah ke perangkat serial AIS.
- `mosquitto` dan `ais-jrc` berstatus `active (running)`.
- Mosquitto mendengarkan `1883`.
- Log `ais-jrc` menampilkan data decode atau pesan koneksi MQTT.

Jika perangkat dicabut dan dipasang lagi, `/dev/ais` harus tetap muncul
(mengikuti serial adapter jika ada, atau port USB fisik jika adapter tidak punya
serial). Jika perangkat belum ada saat boot atau dicabut saat berjalan, proses
tetap aktif dan mencoba membuka serial lagi setiap 3 detik. Setelah dipasang
kembali, pembacaan dan publish MQTT dilanjutkan tanpa restart proses. systemd
tetap memulai ulang proses setelah 10 detik jika proses berhenti karena sebab lain.

### 5. Uji MQTT dari Latte

```sh
sudo apt install -y mosquitto-clients
mosquitto_sub -h localhost -p 1883 \
    -u ais_reader -P '<password ais_reader>' \
    -t 'ais/#' -F '%I %t %p'
```

Untuk melihat satu topik:

```sh
mosquitto_sub -h localhost -p 1883 -u ais_reader \
    -P '<password ais_reader>' -t 'ais/aivdo/position'
```

## Melihat dari Mac dengan MQTT Explorer

Pastikan Mac dan Latte berada di jaringan yang sama, lalu buat koneksi baru:

- **Host:** IP atau hostname Latte yang dapat diakses dari Mac (lihat `hostname -I`
  di Latte untuk IP); `localhost` hanya jika broker berjalan di Mac itu sendiri
- **Port:** `1883`
- **Username:** `ais_reader`
- **Password:** password `ais_reader` yang dimasukkan saat instalasi
- **Encryption/TLS:** nonaktif
- **Validate certificate / CA:** tidak perlu
- **Subscription/topic:** `ais/#`

`ais/#` menampilkan ketiga topik. Untuk satu jenis data, subscribe hanya ke
`ais/aivdm/raw`, `ais/aivdo/position`, atau `ais/aivdm/decoded`.

## Membatasi akses jaringan

MQTT di mode ini tidak terenkripsi, jadi username, password, dan data dapat
terbaca di jaringan. Gunakan hanya pada LAN tepercaya. Broker mendengarkan di
semua antarmuka; batasi akses `1883/tcp` di firewall ke IP penerima, jangan
teruskan port ke internet, dan pisahkan jaringan AIS dari Wi-Fi publik. Contoh
dengan UFW, jika UFW dipakai:

```sh
sudo ufw allow from 192.168.1.20 to any port 1883 proto tcp
sudo ufw status numbered
```

Ganti `192.168.1.20` dengan IP komputer penerima.

## Akun subscriber tambahan

Jika pihak lain juga akan subscribe, buat akun baca-saja terpisah. Password
diminta oleh skrip:

```sh
cd ais_jrc
sudo bash deploy/add_subscriber.sh penerima_ais
```

Jangan membagikan akun `ais_serial` kepada penerima; akun itu untuk service
publisher dan hanya boleh menulis.

## Perintah operasional

```sh
systemctl status mosquitto ais-jrc      # status layanan
journalctl -u ais-jrc -f                # log pembaca AIS
journalctl -u mosquitto -f              # log broker
sudo systemctl restart ais-jrc          # restart pembaca
sudo systemctl restart mosquitto        # restart broker
ls -l /dev/ais                          # nama perangkat tetap
```

Jika repo diperbarui, dari Latte:

```sh
cd ais_jrc
git pull
sudo bash deploy/install_latte.sh
```

Masukkan password yang sama saat diminta, atau beri tahu pihak terkait jika
password diubah. Installer menimpa file konfigurasi broker milik paket ini dan
mengaktifkan ulang layanan.

## Menjalankan manual untuk pengembangan

Contoh ini memakai Mosquitto di mesin yang sama dengan pembaca AIS:

```sh
python3 -m pip install -r requirements.txt
export MQTT_HOST=localhost
export MQTT_PORT=1883
export MQTT_USERNAME=ais_serial
export MQTT_PASSWORD='<password broker>'
python3 decode.py --port /dev/ttyUSB0 --baudrate 38400
```

Jika broker berjalan di mesin lain, isi `MQTT_HOST` dengan IP atau hostname
mesin broker tersebut. `localhost` selalu menunjuk ke mesin tempat aplikasi
dijalankan.

### Deteksi otomatis dan sambung ulang (macOS / Ubuntu)

Kedua pembaca (`decode.py` dan `serial_ais.py`) memakai mekanisme reconnect
yang sama. Lihat identitas adapter tanpa membuka port atau koneksi MQTT:

```sh
python3 decode.py --list-ports
```

Untuk satu adapter USB-serial, port dapat dideteksi otomatis:

```sh
python3 decode.py --port auto --baudrate 38400
# --port auto adalah default jika --port tidak diberikan
```

Untuk pemakaian tetap, pilih identitas adapter yang benar dari output
`--list-ports`. Contoh FT231X (ganti serial dengan milik perangkat):

```sh
python3 decode.py --serial-number DU0ERIKF --vid 0403 --pid 6015
python3 serial_ais.py --serial-number DU0ERIKF --retry-interval 3
```

`--vid` dan `--pid` memakai angka heksadesimal. Mode otomatis hanya memilih
USB-serial, bukan Bluetooth atau UART internal. Jika lebih dari satu adapter
cocok, program menunggu sambil meminta filter yang lebih spesifik di log;
program tidak memilih perangkat secara acak. Setelah koneksi pertama berhasil,
identitas adapter diingat selama proses berjalan, sehingga perubahan nama
seperti `/dev/cu.usbserial-*` di macOS atau `/dev/ttyUSB0` menjadi
`/dev/ttyUSB1` di Ubuntu tetap didukung. Filter `--serial-number` memastikan
pilihan tetap sama setelah aplikasi direstart juga.

Adapter tanpa serial unik mengikuti lokasi USB fisik (`--location`, sesuai
output `--list-ports`); gunakan port USB yang sama. Jika tidak ada serial maupun
lokasi, gunakan path eksplisit yang stabil. Filter identitas hanya dapat
digunakan dengan mode `auto`. Path eksplisit seperti `--port /dev/ais` tetap
dicoba ulang apa adanya; perubahan targetnya ditangani udev. Path bernomor
seperti `/dev/ttyUSB0` tidak mengikuti perubahan nomor secara otomatis.

Koneksi MQTT tetap aktif saat serial terputus. Fragmen AIS yang belum lengkap
dibuang saat serial tersambung ulang. Timeout baca biasa tanpa data tidak
dianggap sebagai disconnect selama perangkat masih terdeteksi. Data yang
dikirim selama perangkat tidak terhubung tidak dapat dipulihkan. `Ctrl+C` atau
`SIGTERM` menutup serial dan koneksi MQTT dengan bersih. Error izin/busy dicatat
dan dicoba ulang; di Ubuntu pastikan user pembaca masuk grup `dialout`.

Installer Ubuntu menyalin modul reconnect bersama aplikasi. Untuk memperbarui
instalasi lama, jalankan ulang installer seperti pada bagian operasional;
service tetap memakai `/dev/ais` dan tidak memerlukan perubahan argumen.

### Menjalankan otomatis di macOS

Template `deploy/com.ais-jrc.reader.plist` adalah LaunchAgent yang mulai saat
user login. Buat virtual environment dan pasang dependensi terlebih dahulu:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ~/Library/LaunchAgents
cp deploy/com.ais-jrc.reader.plist ~/Library/LaunchAgents/
```

Edit salinan plist: ganti semua `/ABSOLUTE/PATH/ais_jrc` dengan path repo
sebenarnya, serta `REPLACE_WITH_USB_SERIAL_NUMBER` dengan serial adapter.
Jika memakai virtual environment bernama `venv`, sesuaikan path Python juga.
Untuk MQTT, isi dictionary `EnvironmentVariables` dengan `MQTT_HOST`,
`MQTT_USERNAME`, `MQTT_PASSWORD`, dan bila perlu `MQTT_PORT`. Contoh format entri:

```xml
<key>MQTT_HOST</key><string>localhost</string>
```

Gunakan `localhost` jika Mosquitto berjalan di Mac yang sama, atau IP/hostname
Latte jika broker berjalan di Latte.

Variabel terminal tidak otomatis diwariskan ke launchd. Tanpa `MQTT_HOST`,
aplikasi hanya decode ke log. Setelah mengedit template:

```sh
chmod 600 ~/Library/LaunchAgents/com.ais-jrc.reader.plist
plutil -lint ~/Library/LaunchAgents/com.ais-jrc.reader.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ais-jrc.reader.plist
launchctl print gui/$(id -u)/com.ais-jrc.reader
```

Log mengikuti path `StandardOutPath` / `StandardErrorPath` di plist. Untuk
menghentikan atau memuat ulang setelah mengedit plist:

```sh
launchctl bootout gui/$(id -u)/com.ais-jrc.reader
# Jalankan kembali perintah bootstrap untuk memuat konfigurasi baru.
```

LaunchAgent membutuhkan sesi login. Untuk Mac yang harus mulai sebelum login,
pasang sebagai LaunchDaemon dengan user dan path yang sesuai kebijakan mesin.

Filter output decode: `--type`, `--mmsi`, `--msg-type`, `--position-only`, dan
`--bbox`. `ais/aivdm/raw` dan `ais/aivdo/position` tetap dipublish tanpa filter;
filter hanya berlaku untuk output terminal dan `ais/aivdm/decoded`. AIVDO
tidak dipublish ke `ais/aivdm/decoded` agar tidak ganda dengan topik posisi. TLS tetap
didukung untuk broker lain dengan `MQTT_TLS=1`, `MQTT_PORT=8883`, dan
`MQTT_CA_CERTS=/lokasi/ca.crt`.

Untuk tes tanpa perangkat/broker:

```sh
python3 -m unittest -v test_decode test_serial_connection test_install_latte
```

## Pembagian file

- `serial_ais.py`: pembaca serial sederhana, menampilkan data mentah tanpa decode/MQTT.
- `serial_connection.py`: deteksi USB-serial dan reconnect untuk macOS/Linux.
- `decode.py`: pembaca serial, penggabungan fragmen, validasi posisi, filter,
  dan hasil decode AIS di terminal.
- `mqtt_publisher.py`: konfigurasi broker, autentikasi, dan publish tiga topik.
- `deploy/install_latte.sh` dan `deploy/ais-jrc.service`: installer Linux dan
  layanan yang berjalan otomatis saat boot.
- `deploy/detect_ais_device.sh`: pemilihan adapter AIS dari identitas USB di Ubuntu.
- `deploy/add_subscriber.sh`: pembuatan akun broker baca-saja tambahan.
- `test_decode.py`: pengujian alur AIS dan publisher tanpa perangkat/broker.
- `test_serial_connection.py`: pengujian pemilihan adapter, reconnect, dan shutdown.
- `test_install_latte.py`: pengujian deteksi adapter installer dengan data udev simulasi.
- `deploy/com.ais-jrc.reader.plist`: template LaunchAgent macOS.
- `requirements.txt`: daftar dependensi Python.
