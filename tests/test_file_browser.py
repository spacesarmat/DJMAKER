"""Тесты открытия расположения аудиофайла в системном файловом менеджере."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from djmaker.services.file_browser import reveal_command, reveal_file


class FileBrowserTests(unittest.TestCase):
    def test_windows_reveal_uses_explorer_select(self) -> None:
        command = reveal_command(Path("music/track.mp3"), system="Windows")

        self.assertEqual("explorer.exe", command[0])
        self.assertEqual("/select,", command[1])
        self.assertTrue(command[2].endswith("track.mp3"))

    def test_macos_reveal_uses_finder_reveal(self) -> None:
        command = reveal_command(Path("music/track.flac"), system="Darwin")

        self.assertEqual("open", command[0])
        self.assertEqual("-R", command[1])
        self.assertTrue(command[2].endswith("track.flac"))

    def test_other_system_opens_parent_directory(self) -> None:
        command = reveal_command(Path("music/track.wav"), system="Linux")

        self.assertEqual("xdg-open", command[0])
        self.assertTrue(command[1].endswith("music"))

    @patch("djmaker.services.file_browser.subprocess.Popen")
    @patch("djmaker.services.file_browser.platform.system", return_value="Darwin")
    def test_reveal_file_starts_non_blocking_process(
        self,
        _system_mock: object,
        popen_mock: object,
    ) -> None:
        reveal_file(Path("music/track.m4a"))

        popen_mock.assert_called_once()
        command = popen_mock.call_args.args[0]
        self.assertEqual(["open", "-R"], command[:2])


if __name__ == "__main__":
    unittest.main()
