import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.uname().sysname == "Darwin", "macOS signing tools required")
class AppPackagingTests(unittest.TestCase):
    def test_install_strips_detritus_and_preserves_previous_app_if_verification_fails(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "apps/desktop-macos/scripts/publish_app.sh"
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            source = temporary / "source/Cleo.app"
            executable = source / "Contents/MacOS/CleoOverlay"
            executable.parent.mkdir(parents=True)
            shutil.copyfile("/usr/bin/true", executable)
            executable.chmod(0o755)
            info = source / "Contents/Info.plist"
            info.write_bytes(plistlib.dumps({
                "CFBundleExecutable": "CleoOverlay",
                "CFBundleIdentifier": "ai.cleo.packaging-test",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
            }))
            subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(source)], check=True, capture_output=True)
            subprocess.run(["xattr", "-wx", "com.apple.FinderInfo", "00" * 32, str(source)], check=True)
            subprocess.run(["xattr", "-w", "com.apple.ResourceFork", "unwanted metadata", str(info)], check=True)

            installed = temporary / "Applications/Cleo.app"
            alias = temporary / "dist/Cleo.app"
            environment = dict(os.environ, CLEO_STOP_RUNNING_APP="0")
            subprocess.run([str(script), str(source), str(installed), str(alias)], env=environment, check=True, capture_output=True)
            self.assertTrue(alias.is_symlink())
            self.assertEqual(alias.resolve(), installed.resolve())
            installed_attributes = subprocess.run(["xattr", str(installed)], check=True, capture_output=True, text=True).stdout
            info_attributes = subprocess.run(["xattr", str(installed / "Contents/Info.plist")], check=True, capture_output=True, text=True).stdout
            self.assertNotIn("com.apple.FinderInfo", installed_attributes)
            self.assertNotIn("com.apple.ResourceFork", info_attributes)
            subprocess.run(["codesign", "--verify", "--strict", str(installed)], check=True, capture_output=True)

            previous_info = (installed / "Contents/Info.plist").read_bytes()
            info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "tampered"}))
            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run([str(script), str(source), str(installed), str(alias)], env=environment, check=True, capture_output=True)
            self.assertEqual((installed / "Contents/Info.plist").read_bytes(), previous_info)
            self.assertTrue(alias.is_symlink())
            self.assertEqual(list(installed.parent.glob(".cleo-install.*")), [])


if __name__ == "__main__":
    unittest.main()
