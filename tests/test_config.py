from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from djmaker.config import default_data_dir


class ConfigTests(unittest.TestCase):
    def test_env_override_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"DJMAKER_DATA_DIR": temp}, clear=False):
                self.assertEqual(Path(temp).resolve(), default_data_dir())


if __name__ == "__main__":
    unittest.main()
