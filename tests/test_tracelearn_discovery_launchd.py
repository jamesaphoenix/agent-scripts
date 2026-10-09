import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DiscoveryLaunchdTests(unittest.TestCase):
    def bootstrap(self, statuses):
        source = (ROOT / 'agent-loops/tracelearn-tutor-discovery/install-launchd.sh').read_text()
        function = source.split('# BEGIN bootstrap_launch_agent\n', 1)[1].split('# END bootstrap_launch_agent', 1)[0]
        fixture = '''
bootstrap_calls=0
bootout_calls=0
sleep_calls=0
statuses=(%s)
launchctl() {
  if [[ "$1" == bootout ]]; then
    bootout_calls=$((bootout_calls + 1))
    return 113
  fi
  [[ "$1" == bootstrap && "$2" == gui/501 && "$3" == /test/job.plist ]] || return 99
  local result=${statuses[$bootstrap_calls]:-5}
  bootstrap_calls=$((bootstrap_calls + 1))
  return "$result"
}
sleep() { sleep_calls=$((sleep_calls + 1)); }
%s
bootstrap_launch_agent gui/501 test-label /test/job.plist
result=$?
printf '%%s %%s %%s %%s' "$result" "$bootstrap_calls" "$bootout_calls" "$sleep_calls"
''' % (' '.join(str(s) for s in statuses), function)
        result = subprocess.run(['/bin/bash', '-c', fixture], check=True, capture_output=True, text=True)
        return tuple(int(value) for value in result.stdout.split())

    def test_registration_teardown_race_recovers_without_repeated_bootout(self):
        self.assertEqual(self.bootstrap([5, 0]), (0, 2, 1, 1))

    def test_successful_first_registration_needs_no_wait(self):
        self.assertEqual(self.bootstrap([0]), (0, 1, 1, 0))

    def test_non_transient_error_is_not_retried(self):
        self.assertEqual(self.bootstrap([125]), (125, 1, 1, 0))

    def test_persistent_io_error_has_a_finite_retry_limit(self):
        self.assertEqual(self.bootstrap([5] * 20), (5, 15, 1, 14))


if __name__ == '__main__':
    unittest.main()
