import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import convert_r2


class ReadyFilterTests(unittest.TestCase):
    def test_unmarked_recording_never_reaches_bundle_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            episode = Path(directory)
            args = SimpleNamespace(input=[str(episode)], ready_only=True, inspect=False)
            with patch.object(convert_r2, "discover", return_value=[episode]), \
                 patch.object(convert_r2, "make_plan", side_effect=AssertionError("active source parsed")) as plan:
                convert_r2.convert(args)
            plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
