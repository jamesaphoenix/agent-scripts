"""Runs the pi-extensions Node test suites so `python3 -m unittest discover -s tests` covers them.

Unit tests: tests/pi-extensions/*.test.ts (node --test with TypeScript type stripping, Node 22.6+).
End-to-end: tests/pi-extensions/e2e-failover.test.mjs (real `pi -p` against fake local providers;
skips itself when pi is not installed).
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "pi-extensions"


def node_major() -> int:
    node = shutil.which("node")
    if not node:
        return 0
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    try:
        return int(out.lstrip("v").split(".")[0])
    except ValueError:
        return 0


@unittest.skipIf(node_major() < 22, "needs Node 22.6+ for TypeScript type stripping")
class PiExtensionTests(unittest.TestCase):
    def run_node_tests(self, *files: Path) -> None:
        # Node 22 needs the flag; 23.6+ strips types by default.
        flags = ["--experimental-strip-types", "--no-warnings"] if node_major() == 22 else []
        proc = subprocess.run(
            ["node", *flags, "--test", *[str(f) for f in files]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            self.fail(f"node --test failed\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}")

    def test_unit(self):
        self.run_node_tests(*sorted(SUITE.glob("*.test.ts")))

    def test_failover_e2e_with_fake_providers(self):
        self.run_node_tests(SUITE / "e2e-failover.test.mjs")


if __name__ == "__main__":
    unittest.main()
