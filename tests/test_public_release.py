"""Exercise the publication guard against synthetic leaks in disposable repos."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_public_release.py"


class PublicReleaseTests(unittest.TestCase):
    def check(self, name, data, *, symlink=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copyfile(CHECKER, root / "scripts/check_public_release.py")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if symlink:
                os.symlink("scripts/check_public_release.py", path)
            else:
                path.write_bytes(data)
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            return subprocess.run([sys.executable, "scripts/check_public_release.py"],
                                  cwd=root, capture_output=True, text=True)

    def test_private_host_is_rejected_without_echoing_it(self):
        host = "fictional-instance." + "pikapod.net"
        result = self.check("example.md", ("https://" + host).encode())
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(host, result.stdout + result.stderr)

    def test_unknown_extension_cannot_hide_a_token(self):
        token = "gh" + "p_" + "A" * 36
        result = self.check("notes.backup", token.encode())
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_data_archive_binary_and_symlink_are_rejected(self):
        for name, data in (("evidence.zip", b"PK\x00"), ("data/capture.json", b"{}"),
                           ("settings.json", b"\x00\xff")):
            with self.subTest(name=name):
                self.assertNotEqual(self.check(name, data).returncode, 0)
        self.assertNotEqual(self.check("linked.md", b"", symlink=True).returncode, 0)

    def test_reserved_example_address_and_ordinary_text_are_allowed(self):
        result = self.check("guide.md", b"https://mealie.example.test user@example.test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_personal_email_is_rejected(self):
        address = "fictional-person@" + "mail.invalid-provider.org"
        self.assertNotEqual(self.check("guide.md", address.encode()).returncode, 0)

    def test_changed_screenshot_fails_integrity_check(self):
        self.assertNotEqual(self.check("assets/promotion-review-interface.png", b"fake").returncode, 0)


if __name__ == "__main__":
    unittest.main()
