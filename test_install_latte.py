"""Test Ubuntu installer discovery without touching devices or installing packages."""

from pathlib import Path
import shlex
import subprocess
import unittest


DETECTOR = Path(__file__).resolve().parent / "deploy" / "detect_ais_device.sh"


def properties(serial="DU0ERIKF", vendor="0403", product="6015"):
    return (f"DEVNAME=/dev/ignored\nID_VENDOR_ID={vendor}\n"
            f"ID_MODEL_ID={product}\nID_SERIAL_SHORT={serial}\n"
            "ID_MODEL=FT231X_USB_UART\nID_VENDOR=FTDI\n")


class InstallerDiscoveryTests(unittest.TestCase):
    def detect(self, ports):
        # udevadm is the only external dependency of detection. Feed realistic
        # property records, with None representing a failed query.
        branches = []
        for device, record in ports.items():
            action = "return 1" if record is None else f"printf '%s' {shlex.quote(record)}"
            branches.append(f"{shlex.quote('--name=' + device)}) {action} ;;")
        script = "\n".join([
            "set -euo pipefail",
            f"source {shlex.quote(str(DETECTOR))}",
            "udevadm() {",
            '    case "$3" in',
            *branches,
            "        *) return 1 ;;",
            "    esac",
            "}",
            'detect_ais_device "$@"',
        ])
        return subprocess.run(["bash", "-c", script, "detect-test", *ports],
                              capture_output=True, text=True)

    def test_selects_exact_adapter_among_other_serial_devices(self):
        result = self.detect({
            "/dev/ttyUSB0": properties(serial="OTHER"),
            "/dev/ttyUSB1": properties(),
            "/dev/ttyACM0": properties(vendor="1234", product="5678"),
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/dev/ttyUSB1")

    def test_detects_without_by_id_links_or_python_serial_dependency(self):
        for device in ("/dev/ttyUSB0", "/dev/ttyUSB7", "/dev/ttyACM2"):
            with self.subTest(device=device):
                result = self.detect({device: properties()})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), device)

    def test_does_not_select_similar_adapter_or_matching_description(self):
        for record in (properties(serial="OTHER"), properties(vendor="1234"),
                       properties(product="6001"), properties(serial="")):
            with self.subTest(record=record):
                result = self.detect({"/dev/ttyUSB0": record})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("DU0ERIKF", result.stderr)

    def test_no_devices_reports_expected_adapter(self):
        result = self.detect({})
        self.assertEqual(result.returncode, 1)
        self.assertIn("0403:6015", result.stderr)

    def test_duplicate_matching_ports_require_explicit_choice(self):
        result = self.detect({"/dev/ttyUSB0": properties(), "/dev/ttyUSB1": properties()})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Lebih dari satu port", result.stderr)
        self.assertIn("--device", result.stderr)

    def test_failed_query_does_not_reuse_previous_device_properties(self):
        result = self.detect({"/dev/ttyUSB0": properties(), "/dev/ttyUSB1": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/dev/ttyUSB0")
        self.assertIn("Tidak dapat membaca", result.stderr)

    def test_property_order_is_irrelevant_and_values_are_not_evaluated(self):
        result = self.detect({"/dev/ttyUSB0": (
            "ID_MODEL=FT231X USB UART\nID_SERIAL_SHORT=DU0ERIKF\n"
            "UNRELATED=$(exit 99)\nID_MODEL_ID=6015\nID_VENDOR_ID=0403\n"
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/dev/ttyUSB0")


if __name__ == "__main__":
    unittest.main()
