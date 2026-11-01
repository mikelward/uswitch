"""`make install` and `make uninstall` run uswitch's own, with the chosen Python."""

import os
import subprocess
import unittest

from support import HERE

ROOT = os.path.dirname(HERE)


def dry_run(*args):
    """The commands `make -n` would run, outside any make this one runs under."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHON", "MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    r = subprocess.run(
        ["make", "--no-print-directory", "-n", "-C", ROOT, *args],
        capture_output=True, text=True, env=env, check=True,
    )
    return r.stdout.splitlines()


class MakefileTest(unittest.TestCase):
    def test_install_and_uninstall_run_uswitchs_own(self):
        self.assertEqual(dry_run("install"), ["python3 ./uswitch install"])
        self.assertEqual(dry_run("uninstall"), ["python3 ./uswitch uninstall"])

    def test_python_picks_the_interpreter(self):
        self.assertEqual(dry_run("install", "PYTHON=/opt/example/python3"), ["/opt/example/python3 ./uswitch install"])


if __name__ == "__main__":
    unittest.main()
