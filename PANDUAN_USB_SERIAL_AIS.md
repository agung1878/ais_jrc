# Panduan identitas USB serial AIS dan `/dev/ais`

Panduan ini berlaku untuk instalasi Linux pada proyek ini. Tujuannya memilih
**adaptor AIS yang benar** ketika ada beberapa perangkat serial, termasuk pada
server lain yang memakai adaptor dengan nomor serial USB berbeda.

Ada dua nama yang perlu dibedakan:

| Nama | Asal | Perilaku |
| --- | --- | --- |
| `/dev/ttyUSB0`, `/dev/ttyUSB1`, atau `/dev/ttyACM0` | Diberikan kernel | Angkanya dapat berubah setelah perangkat dicabut, port dipindah, atau server menyala ulang. |
| `/dev/ais` | Symlink yang dibuat aturan udev proyek ini | Menunjuk ke port perangkat yang **identitasnya cocok** dengan aturan di server tersebut. |

Service menjalankan `decode.py --port /dev/ais --baudrate 38400`. Ia tidak
memilih perangkat berdasarkan angka `ttyUSB` atau nama produk yang terlihat di
layar. `detect_ais_device.sh` hanya membantu **pemilihan saat instalasi**;
aturan udev di `/etc/udev/rules.d/99-ais-jrc.rules` menentukan perangkat mana
yang mendapat symlink `/dev/ais` saat operasi normal.

## 1. Lihat perangkat serial yang tersedia

Pasang adaptor AIS, lalu jalankan di **server yang akan menjalankan service**:

```sh
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
ls -l /dev/serial/by-id/ /dev/serial/by-path/ 2>/dev/null
```

Tidak adanya `/dev/serial/by-id/` tidak menghalangi instalasi; installer
membaca identitas langsung dari udev. Untuk melihat identitas **setiap** port
serial yang ada, jalankan:

```sh
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$dev" ] || continue
    printf '\n%s\n' "$dev"
    udevadm info --query=property --name="$dev" |
        grep -E '^(ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT|ID_PATH|ID_MODEL|ID_VENDOR)='
done
```

Contoh keluaran untuk adaptor yang sebelumnya dipakai proyek ini:

```text
/dev/ttyUSB0
ID_VENDOR_ID=0403
ID_MODEL_ID=6015
ID_SERIAL_SHORT=DU0ERIKF
ID_PATH=pci-0000:00:14.0-usb-0:2:1.0
```

**Angka dan path di atas hanya contoh.** Salin nilai dari perangkat di server
yang sedang diperiksa; jangan menyalin `ID_PATH` contoh ke server lain.

| Properti | Arti | Kegunaan di proyek |
| --- | --- | --- |
| `ID_VENDOR_ID` | ID produsen adaptor USB, empat digit heksadesimal | Dipasangkan dengan product dan serial agar aturan tidak terlalu luas. |
| `ID_MODEL_ID` | ID produk adaptor, empat digit heksadesimal | Membedakan model adaptor. |
| `ID_SERIAL_SHORT` | Nomor serial yang dilaporkan adaptor USB | Pilihan utama jika ada dan benar-benar membedakan perangkat. |
| `ID_PATH` | Jalur/port fisik USB pada server itu | Cadangan jika serial tidak ada atau tidak unik. Nilainya dapat berubah jika perangkat atau hub dipindah. |

Untuk memastikan port mana yang benar, cocokkan keluaran sebelum dan sesudah
adaptor AIS dipasang, atau cabut **hanya adaptor AIS** lalu lihat port mana
yang hilang. Perintah `udevadm info` hanya membaca metadata dan tidak membuka
port serial. Jangan memilih port hanya dari urutan `/dev/ttyUSB0`.

Pada mesin yang diperiksa saat panduan ini ditulis, `/dev/ttyACM0` mempunyai
`ID_VENDOR_ID=3343`, `ID_MODEL_ID=803a`, dan model
`LattePanda_Leonardo`. Itu perangkat internal LattePanda, **bukan** adaptor
FT231X AIS `0403:6015`. Saat itu `/dev/ais` belum ada.

## 2. Instalasi di server baru dengan adaptor berbeda

Jika adaptor belum tersedia, instal broker dan service terlebih dahulu tanpa
komponen `device`:

```sh
sudo bash deploy/install_latte.sh --components broker,service
```

Service akan menunggu `/dev/ais`. Setelah adaptor dipasang dan port AIS
teridentifikasi, buat aturan perangkat saja tanpa meminta ulang password MQTT:

```sh
sudo bash deploy/install_latte.sh --components device --device /dev/ttyUSB1
```

Ganti `/dev/ttyUSB1` dengan port AIS yang sebenarnya. `--help` menampilkan
semua pilihan komponen.

Jika adaptor mempunyai identitas default proyek
`0403:6015 / DU0ERIKF`, installer dapat dijalankan tanpa pilihan perangkat:

```sh
sudo bash deploy/install_latte.sh
```

Jika nomor serialnya berbeda, modelnya berbeda, atau ada beberapa port
serial, pilih **port AIS yang sudah diverifikasi** dengan `--device`:

```sh
sudo bash deploy/install_latte.sh --device /dev/ttyUSB1
```

Ganti `/dev/ttyUSB1` dengan port yang ditemukan pada server tersebut. Opsi
`--device` memilih perangkat **pada saat instalasi**. Setelah itu installer
membaca `ID_VENDOR_ID`, `ID_MODEL_ID`, `ID_SERIAL_SHORT`, dan `ID_PATH` dari
perangkat terpilih, lalu menulis aturan udev untuk membuat `/dev/ais`.
Service tetap membuka `/dev/ais`; tidak perlu mengubah `decode.py` atau unit
systemd untuk setiap nomor serial baru. Masing-masing server mempunyai aturan
udev dan `/dev/ais` sendiri, sehingga identitas pada server A tidak mengubah
server B.

Jika **dua adaptor melaporkan nomor serial USB yang sama**, `--device` tetap
memilih port untuk instalasi, tetapi installer akan membuat aturan berdasarkan
serial tersebut. Aturan itu dapat cocok dengan kedua adaptor. Setelah
instalasi, ubah aturan lokal menjadi berdasarkan `ID_PATH` seperti bagian 4,
lalu pastikan hanya port AIS yang mendapat `/dev/ais`.

Installer hanya menerima port USB serial yang akhirnya mengarah ke
`/dev/ttyUSBN` atau `/dev/ttyACMN`. Untuk menambahkan adaptor pada server
yang sudah terpasang, gunakan `--components device --device /dev/ttyUSBN`:
mode ini tidak menjalankan komponen broker dan tidak mengubah password MQTT.

## 3. Cara aturan `/dev/ais` dibuat

Jika adaptor punya `ID_SERIAL_SHORT` serta vendor/product ID, installer
menulis aturan yang setara dengan contoh ini:

```udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", ENV{ID_SERIAL_SHORT}=="DU0ERIKF", SYMLINK+="ais", GROUP="dialout", MODE="0660"
```

Bagian sebelum `SYMLINK` adalah syarat kecocokan. `SYMLINK+="ais"` membuat
`/dev/ais`; `GROUP="dialout"` dan `MODE="0660"` mengatur izin node serial.
Akun service `ais-jrc` berada dalam grup `dialout`.

Jika nomor serial tidak tersedia, installer memakai `ID_PATH`:

```udev
SUBSYSTEM=="tty", ENV{ID_PATH}=="pci-0000:00:14.0-usb-0:2:1.0", SYMLINK+="ais", GROUP="dialout", MODE="0660"
```

Nilai `ID_PATH` di contoh harus diganti dengan nilai **server dan port USB
yang sebenarnya**. Aturan ini mengikuti **soket USB**, sehingga adaptor lain
yang dipasang ke soket yang sama dapat memperoleh `/dev/ais`. Gunakan
serial USB unik jika tersedia. Jika dua adaptor memiliki serial USB yang sama,
aturan berdasarkan serial dapat mencocokkan keduanya dan pemilik `/dev/ais`
menjadi tidak pasti; pilih `ID_PATH` yang spesifik untuk soket AIS.

## 4. Mengganti adaptor pada server yang sudah berjalan

Langkah ini mengubah **hanya aturan perangkat**. Username/password broker
tidak disentuh.

1. Temukan port adaptor AIS baru dan catat properti udev-nya memakai langkah
   pada bagian 1. Pastikan port yang dipilih memang terhubung ke receiver AIS.
2. Cadangkan aturan lama dan edit aturan di server itu:

   ```sh
   sudo cp -a /etc/udev/rules.d/99-ais-jrc.rules /etc/udev/rules.d/99-ais-jrc.rules.bak
   sudoedit /etc/udev/rules.d/99-ais-jrc.rules
   ```

3. Ganti **satu baris aturan lama** dengan nilai perangkat baru. Jika
   `ID_SERIAL_SHORT` unik, gunakan vendor, product, dan serialnya:

   ```udev
   SUBSYSTEM=="tty", ATTRS{idVendor}=="VENDOR_BARU", ATTRS{idProduct}=="PRODUCT_BARU", ENV{ID_SERIAL_SHORT}=="SERIAL_BARU", SYMLINK+="ais", GROUP="dialout", MODE="0660"
   ```

   `VENDOR_BARU` dan `PRODUCT_BARU` harus berupa empat digit heksadesimal
   dari udev, **tanpa** awalan `0x`. Jika serial tidak ada atau dipakai
   bersama oleh beberapa adaptor, gunakan nilai `ID_PATH` perangkat AIS:

   ```udev
   SUBSYSTEM=="tty", ENV{ID_PATH}=="PATH_USB_AIS_DI_SERVER_INI", SYMLINK+="ais", GROUP="dialout", MODE="0660"
   ```

   Jangan membuat dua aturan berbeda yang sama-sama memberi nama `ais` pada
   dua perangkat di server yang sama.
4. Muat ulang aturan dan proses kembali perangkat yang sudah terpasang:

   ```sh
   sudo udevadm control --reload-rules
   sudo udevadm trigger --action=add --subsystem-match=tty
   sudo udevadm settle
   ```

5. Pastikan symlink benar-benar menuju port yang dipilih:

   ```sh
   ls -l /dev/ais
   readlink -e /dev/ais
   udevadm info --query=property --name="$(readlink -e /dev/ais)" |
       grep -E '^(ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT|ID_PATH)='
   id ais-jrc
   ```

   `readlink -e` harus menghasilkan path port AIS, misalnya
   `/dev/ttyUSB1`. Cocokkan lagi identitasnya dengan langkah 1.

Jika sebelumnya `/dev/ais` tidak ada, service yang masih berjalan akan mencoba
membukanya kembali setiap 3 detik. Jika service sudah terhubung ke adaptor lama
dan adaptor itu masih terpasang, restart service setelah aturan baru diverifikasi
agar ia membuka adaptor baru. Periksa lognya:

```sh
systemctl status ais-jrc.service --no-pager
journalctl -u ais-jrc.service -n 50 --no-pager
```

Cari `Serial connected: /dev/ais at 38400 baud`. Status systemd `active`
sendirian belum membuktikan port sudah terbuka: saat `/dev/ais` tidak ada,
service tetap hidup sambil mencoba lagi. Jika service berhenti, jalankan
`sudo systemctl restart ais-jrc.service`, lalu periksa log kembali.

## 5. Peran identitas di dalam kode Python

- [`deploy/detect_ais_device.sh`](deploy/detect_ais_device.sh) memeriksa
  `0403:6015 / DU0ERIKF` **hanya ketika installer dijalankan tanpa
  `--device`**. Untuk adaptor lain di server baru, gunakan `--device` daripada
  mengganti nilai bawaan yang mungkin masih dipakai server lain.
- [`deploy/install_latte.sh`](deploy/install_latte.sh) membaca properti port
  terpilih dan menulis `/etc/udev/rules.d/99-ais-jrc.rules` **di server itu**.
- [`deploy/ais-jrc.service`](deploy/ais-jrc.service) memberi argumen
  `--port /dev/ais --baudrate 38400` kepada `decode.py`.
- [`serial_connection.py`](serial_connection.py) membuka port tersebut. Jika
  port hilang, ia menunggu dan mencoba lagi. [`decode.py`](decode.py) baru
  memproses kalimat AIS setelah port terbuka.

Untuk pengujian manual, Python juga mendukung pemilihan identitas **tanpa
symlink udev**:

```sh
.venv/bin/python decode.py --port auto --vid 0403 --pid 6015 --serial-number DU0ERIKF
```

Ganti tiga nilai tersebut dengan identitas perangkat yang diuji. Mode itu
berbeda dari service saat ini: `--port auto` mencari port melalui `pyserial`,
sedangkan service memakai `/dev/ais`. Jangan menggabungkan `--port /dev/ais`
dengan `--vid`, `--pid`, atau `--serial-number`; program menolak kombinasi
tersebut. Jangan menjalankan pengujian yang membuka port bersamaan dengan
service, karena keduanya dapat saling berebut data serial.

## 6. Jika `/dev/ais` tetap tidak muncul

| Gejala | Pemeriksaan |
| --- | --- |
| Tidak ada port USB serial AIS | Cek adaptor, kabel, daya, dan apakah port baru muncul setelah dipasang. |
| Ada port, tetapi identitas berbeda dari aturan | Baca `udevadm info` pada port itu; perbarui aturan lokal sesuai bagian 4. |
| Ada dua adaptor dengan serial yang sama | Gunakan `ID_PATH` untuk soket yang benar; satu nama `ais` hanya boleh dimiliki satu port. |
| `/dev/ais` mengarah ke port yang salah | Bandingkan `readlink -e /dev/ais` dengan daftar identitas semua port. |
| `/dev/ais` ada tetapi service mencatat `Permission denied` | Cek `ls -l /dev/ais`, izin target, dan keanggotaan `id ais-jrc`; target perlu dapat dibaca oleh grup `dialout`. |
| Port terbuka tetapi tidak ada hasil decode | Periksa log untuk kalimat gagal checksum/decode, sambungan receiver JRC, dan baud rate `38400`. |

Aturan udev yang dipakai adalah aturan **per server**, bukan bagian dari
payload AIS atau identitas kapal (MMSI). Nomor serial USB adaptor dan MMSI
kapal adalah dua hal yang berbeda.

Rujukan teknis: [udev rules](https://www.freedesktop.org/software/systemd/man/latest/udev.html)
dan [udevadm](https://www.freedesktop.org/software/systemd/man/latest/udevadm.html).
