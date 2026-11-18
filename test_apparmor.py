"""Exercise AppArmor setup with local fixtures; no kernel policy changes."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parent / "deploy" / "configure_mosquitto_apparmor.sh"
RULES = ["/etc/mosquitto/ais-jrc/passwd r,", "/etc/mosquitto/ais-jrc/acl r,"]


class AppArmorSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ais-apparmor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "profile directory"
        self.root.mkdir()
        self.reloads = Path(self.temp.name) / "reloads"

    def profile(self, name="mosquitto", include=None):
        if include is None:
            include = f"include if exists <local/{name}>"
        profile = self.root / name
        profile.write_text(f"profile mosquitto /usr/sbin/mosquitto {{\n  {include}\n}}\n")
        return profile

    def configure(self, reload_status=0):
        script = "\n".join([
            "set -euo pipefail",
            f"source {shlex.quote(str(SCRIPT))}",
            "apparmor_parser() {",
            f"    printf '%s\\n' \"$*\" >> {shlex.quote(str(self.reloads))}",
            f"    return {reload_status}",
            "}",
            f"configure_mosquitto_apparmor {shlex.quote(str(self.root))}",
        ])
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_modern_profile_adds_only_read_rules_and_preserves_package_profile(self):
        profile = self.profile()
        original = profile.read_text()
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        local = self.root / "local" / "mosquitto"
        self.assertEqual(local.read_text().split(), "\n".join(RULES).split())
        self.assertEqual(local.stat().st_mode & 0o777, 0o644)
        self.assertEqual(profile.read_text(), original)
        self.assertEqual(self.reloads.read_text().strip(), f"-r {profile}")

    def test_legacy_profile_include_and_existing_overrides_are_preserved(self):
        self.profile("usr.sbin.mosquitto", "#include <local/usr.sbin.mosquitto>")
        local = self.root / "local" / "usr.sbin.mosquitto"
        local.parent.mkdir()
        existing = "# Existing local customization\n/srv/mqtt/custom r,"
        local.write_text(existing)
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(local.read_text().startswith(existing + "\n"))
        self.assertTrue(all(rule in local.read_text() for rule in RULES))

    def test_repeat_execution_does_not_duplicate_rules(self):
        self.profile()
        self.assertEqual(self.configure().returncode, 0)
        first = (self.root / "local" / "mosquitto").read_text()
        self.assertEqual(self.configure().returncode, 0)
        self.assertEqual((self.root / "local" / "mosquitto").read_text(), first)
        for rule in RULES:
            self.assertEqual(first.count(rule), 1)

    def test_does_not_load_an_administrator_disabled_profile(self):
        self.profile()
        disabled = self.root / "disable" / "mosquitto"
        disabled.parent.mkdir()
        disabled.symlink_to(self.root / "mosquitto")
        self.assertEqual(self.configure().returncode, 0)
        self.assertFalse(self.reloads.exists())
        self.assertFalse((self.root / "local").exists())

    def test_missing_profile_is_a_noop(self):
        self.assertEqual(self.configure().returncode, 0)
        self.assertFalse(self.reloads.exists())
        self.assertFalse((self.root / "local").exists())

    def test_missing_local_include_fails_without_changing_package_profile(self):
        profile = self.profile(include="# Custom profile without a local include")
        original = profile.read_text()
        result = self.configure()
        self.assertEqual(result.returncode, 1)
        self.assertIn("include if exists <local/mosquitto>", result.stderr)
        self.assertEqual(profile.read_text(), original)
        self.assertFalse(self.reloads.exists())

    def test_failed_profile_reload_stops_setup(self):
        self.profile()
        result = self.configure(reload_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Gagal memuat ulang", result.stderr)
        self.assertNotIn("diizinkan", result.stdout)


if __name__ == "__main__":
    unittest.main()
