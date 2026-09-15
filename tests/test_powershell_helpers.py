from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PowerShellHelperTests(unittest.TestCase):
    def test_essentia_trigger_is_ascii_safe_for_windows_powershell(self) -> None:
        path = PROJECT_ROOT / "scripts" / "trigger_essentia_build.ps1"
        payload = path.read_bytes()

        self.assertTrue(payload, "PowerShell helper must not be empty")
        self.assertTrue(
            all(byte < 128 for byte in payload),
            "trigger_essentia_build.ps1 must stay ASCII-only for Windows PowerShell 5.1",
        )

    def test_essentia_trigger_avoids_fragile_backtick_continuations(self) -> None:
        path = PROJECT_ROOT / "scripts" / "trigger_essentia_build.ps1"
        source = path.read_text(encoding="ascii")

        self.assertNotIn("`", source)
        self.assertIn("gh workflow run", source)
        self.assertIn("gh run watch", source)
        self.assertIn("-notcontains", source)


if __name__ == "__main__":
    unittest.main()
