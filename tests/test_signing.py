import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class SigningSetupTests(unittest.TestCase):
    def test_setup_reuses_certificate_without_reimporting_or_changing_user_keychain(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            commands = temporary / "commands"
            commands.mkdir()
            # Test real certificate generation with a fake Keychain interface.
            security = commands / "security"
            security.write_text("""#!/bin/zsh
set -e
print -r -- "$1" >> "$TEST_SECURITY_LOG"
if [[ "$1" == "find-identity" ]]; then
  hash=$(openssl x509 -in "$CLEO_SIGNING_DIR/identity.pem" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')
  print -r -- "1) $hash Cleo Local Development"
fi
""")
            security.chmod(0o755)
            environment = dict(os.environ)
            environment.update({
                "PATH": str(commands) + os.pathsep + environment["PATH"],
                "CLEO_SIGNING_DIR": str(temporary / "signing"),
                "CLEO_SIGNING_KEYCHAIN": str(temporary / "fake.keychain"),
                "TEST_SECURITY_LOG": str(temporary / "security.log"),
                "TMPDIR": str(temporary),
            })
            script = root / "apps/desktop-macos/scripts/setup_signing.sh"
            subprocess.run([str(script)], env=environment, check=True, capture_output=True, text=True)
            certificate = temporary / "signing/identity.pem"
            original = certificate.read_bytes()
            details = subprocess.run(["openssl", "x509", "-in", str(certificate), "-noout", "-text"], check=True, capture_output=True, text=True).stdout
            self.assertIn("Code Signing", details)
            self.assertIn("CA:FALSE", details)
            subprocess.run([str(script)], env=environment, check=True, capture_output=True, text=True)
            self.assertEqual(certificate.read_bytes(), original)
            calls = (temporary / "security.log").read_text().splitlines()
            self.assertEqual(calls.count("import"), 1)
            self.assertEqual(calls.count("add-trusted-cert"), 1)
            self.assertFalse((temporary / "signing/setup.lock").exists())
            self.assertEqual(list(temporary.rglob("*.p12")), [])
            self.assertEqual(list(temporary.rglob("key.pem")), [])


if __name__ == "__main__":
    unittest.main()
