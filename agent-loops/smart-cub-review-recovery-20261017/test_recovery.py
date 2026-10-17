import importlib.util
import io
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import contextmanager

ROOT = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class RecoveryTests(unittest.TestCase):
    def test_prior_completions_are_disjoint_exact_and_complete(self):
        m = load('recovery')
        rows = [{'id': 'a', 'aspect': 'wide', 'observedComplete': True},
                {'id': 'a', 'aspect': 'tall', 'observedComplete': True}]
        self.assertEqual(m.completed_keys(rows), {('a', 'wide'), ('a', 'tall')})
        with self.assertRaises(ValueError):
            m.completed_keys(rows + [rows[0]])
        with self.assertRaises(ValueError):
            m.completed_keys([{**rows[0], 'observedComplete': False}])

    def test_only_proven_preflight_failure_may_be_reentered(self):
        m = load('recovery')
        row = {'identity': 'a', 'aspect': 'tall', 'outputSha256': 'a' * 64,
               'requestCreatedByThisAttempt': False, 'providerResponseForThisAttempt': None,
               'safeError': '1Password Gemini key read failed; no paid request submitted'}
        intent = {'id': 'a', 'aspect': 'tall', 'outputSha256': 'a' * 64}
        m.no_submission(row, intent)
        for change in [{'requestCreatedByThisAttempt': True}, {'identity': 'b'},
                       {'providerResponseForThisAttempt': '/response'}, {'safeError': 'network timeout'}]:
            with self.assertRaises(ValueError):
                m.no_submission({**row, **change}, intent)

    def test_existing_or_unresolved_paid_job_refuses_new_submission(self):
        m = load('recovery')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p = folder / 'v/request-one.json'
            p.parent.mkdir()
            for state in ['submitted_unknown', 'reserved', 'completed_advisory']:
                p.write_text(json.dumps({'state': state, 'reservation_id': 'held'}))
                if state == 'completed_advisory':
                    m.refuse_uncertain(folder)
                else:
                    with self.assertRaises(ValueError):
                        m.refuse_uncertain(folder)

    def test_changed_prior_request_and_checkpoint_refused(self):
        m = load('recovery')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'proof'
            p.write_text('known')
            row = m.ref(p)
            m.current(row)
            p.write_text('changed')
            with self.assertRaises(ValueError):
                m.current(row)

    def test_exact_command_shim_rejects_other_secret_or_arguments(self):
        m = load('credential_shim')
        with patch.dict('os.environ', {'SMART_CUB_REVIEW_GEMINI_KEY': 'fixture-only'}, clear=True):
            self.assertEqual(m.secret_for(['read', 'op://api-keys/Gemini/api_key']), 'fixture-only')
            for argv in [['read', 'op://api-keys/Other/api_key'], ['read', 'op://api-keys/Gemini/api_key', '--format=json'], ['vault', 'list']]:
                with self.assertRaises(ValueError):
                    m.secret_for(argv)
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                m.secret_for(['read', 'op://api-keys/Gemini/api_key'])

    def test_single_memory_load_has_no_retry_or_secret_error_output(self):
        m = load('recovery')
        from types import SimpleNamespace
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='fixture sensitive text')) as call:
            with self.assertRaisesRegex(ValueError, '^Credential preflight failed; no provider child started$'):
                m.memory_key(Path('/task/config'))
            self.assertEqual(call.call_count, 1)
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='fixture-only\n', stderr='')):
            self.assertEqual(m.memory_key(Path('/task/config')), 'fixture-only')

    def test_latest_budget_can_add_legitimate_jobs_but_cap_and_original_rows_stay_exact(self):
        m = load('recovery')
        protected = {'settled-old': {'state': 'settled', 'actual_cents': 2}}
        value = {'budget': {'currency': 'USD', 'cap_cents': 10000,
                            'reservations': {**protected, 'later-unrelated': {'state': 'reserved'}}}}
        m.budget_binding(value, protected)
        value['budget']['reservations']['settled-old'] = {'state': 'reserved'}
        with self.assertRaises(ValueError):
            m.budget_binding(value, protected)
        value['budget']['reservations'].update(protected)
        value['budget']['cap_cents'] = 20000
        with self.assertRaises(ValueError):
            m.budget_binding(value, protected)

    def test_credential_gate_is_explicit_utc_and_blocks_early_read(self):
        m = load('recovery')
        at = m.datetime.datetime.fromisoformat('2026-10-17T15:20:00+00:00')
        config = {'credentialReadNotBeforeUtc': '2026-10-17T15:21:00+00:00'}
        self.assertEqual(m.credential_delay(config, at), 60)
        self.assertEqual(m.credential_delay(config, at + m.datetime.timedelta(seconds=90)), 0)
        with self.assertRaises(ValueError):
            m.credential_delay({'credentialReadNotBeforeUtc': '2026-10-17T15:21:00'}, at)

    def test_freshness_and_uncertainty_rechecked_after_guard_before_child(self):
        text = (ROOT / 'recovery.py').read_text()
        inside = text[text.index('with guard.admission('):]
        self.assertLess(inside.index('adapter.fresh('), inside.index('child = lease.popen('))
        self.assertLess(inside.index('refuse_uncertain('), inside.index('child = lease.popen('))
        self.assertIn("if key in inherited:", text)
        self.assertIn("raise ValueError('Existing recovery checkpoint; no automatic restart')", text)

    def run_fake_recovery(self, *, drift=False, incomplete=False, wait=False):
        m = load('recovery')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        status = root / 'new/status.json'
        config = {'family': 'marketing', 'status': str(status), 'stopPath': str(root / 'STOP'),
                  'credentialConfig': str(root / 'op-config'), 'shim': {'path': str(root / 'shim.py')},
                  'credentialReadNotBeforeUtc': '2026-10-16T00:00:00+00:00',
                  'legacyStatus': {'path': '/retained-old-status'}, 'guard': {'path': str(root / 'guard.py')}}
        config_path, release = root / 'config.json', root / 'release.json'
        config_path.write_text(json.dumps(config))
        release.write_text(json.dumps({'contract': 'root-explicit-review-recovery-release/v1',
                                      'configSha256': m.sha(config_path), 'family': 'marketing',
                                      'allowSingleNewParent': True}))
        saved = {'id': 'a', 'aspect': 'wide', 'observedComplete': True,
                 'request': {'path': '/old/a'}, 'log': {'path': '/old/log'},
                 'result': {'id': 'a', 'aspect': 'wide'}, 'qualityApproved': False}
        old = {'completed': [saved]}
        calls, fresh = [], []

        class FakeAdapter:
            def __init__(self, value):
                self.app = root
                self.jobs = [{'id': x, 'aspect': 'wide'} for x in ['a', 'b', 'c']]
                self.original = SimpleNamespace(save=lambda p, d: p.write_text(json.dumps(d)))

            def ready(self, row):
                return {'output': {'sha256': row['id'] * 64}}

            def intent(self, row, ready):
                return {'id': row['id'], 'aspect': row['aspect'], 'outputSha256': ready['output']['sha256']}

            def fresh(self, row, ready):
                fresh.append(row['id'])
                if drift:
                    raise ValueError('fixture drift inside paid lease')

            def command(self, row):
                return ['fixture-only', row['id']]

            def record(self, row, ready, result, request, log):
                if row['id'] == 'a':
                    return saved
                return {'id': row['id'], 'aspect': row['aspect'],
                        'observedComplete': not incomplete, 'qualityApproved': False}

        class Lease:
            def popen(self, argv, **kwargs):
                calls.append(argv[1])
                selftest.assertEqual(kwargs['env']['SMART_CUB_REVIEW_GEMINI_KEY'], 'fixture-memory-only')
                kwargs['stdout'].write(json.dumps({'id': argv[1], 'aspect': 'wide',
                                                   'evidence': str(root / 'request.json')}) + '\n')
                kwargs['stdout'].flush()
                return SimpleNamespace(pid=123, wait=lambda: 0)

        selftest = self

        @contextmanager
        def admission(*args, **kwargs):
            yield Lease()

        delays = [60, 60, 0] if wait else [0]
        observed_waits = []

        def fake_sleep(seconds):
            self.assertEqual(key_read.call_count, 0)
            self.assertEqual(calls, [])
            observed_waits.append(json.loads(status.read_text())['state'])

        with patch.object(m, 'check', return_value=(config, old)), \
                patch.object(m, 'Adapter', FakeAdapter), \
                patch.object(m, 'memory_key', return_value='fixture-memory-only') as key_read, \
                patch.object(m, 'credential_delay', side_effect=lambda _: delays.pop(0) if delays else 0), \
                patch.object(m.time, 'sleep', side_effect=fake_sleep), \
                patch.object(m, 'current', side_effect=lambda row: Path(row['path'])), \
                patch.object(m, 'load', return_value=SimpleNamespace(admission=admission)), \
                patch.object(m.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'), \
                patch.object(m.os, 'getpriority', return_value=10):
            if drift:
                with self.assertRaisesRegex(ValueError, 'fixture drift'):
                    m.execute(config_path, release, m.sha(release))
            else:
                m.execute(config_path, release, m.sha(release))
            self.assertEqual(key_read.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'Existing recovery checkpoint'):
                m.execute(config_path, release, m.sha(release))
            self.assertEqual(key_read.call_count, 1)
        text = status.read_text()
        self.assertNotIn('fixture-memory-only', text)
        value = json.loads(text)
        self.assertEqual(value['inheritedCompleted'], [saved])
        self.assertFalse(value['qualityApproved'])
        if wait:
            self.assertEqual(observed_waits, ['waiting_for_credential_not_before_gate'])
        return calls, fresh, value

    def test_exact_prior_completion_skipped_and_only_remaining_children_admitted_once(self):
        calls, fresh, value = self.run_fake_recovery()
        self.assertEqual(calls, ['b', 'c'])
        self.assertEqual(len(fresh), 4)
        self.assertEqual(value['state'], 'complete_advisories_agent_judgements_pending')

    def test_graph_drift_inside_lease_starts_no_child(self):
        calls, _, value = self.run_fake_recovery(drift=True)
        self.assertEqual(calls, [])
        self.assertEqual(value['state'], 'halted_requires_inspection_no_retry')

    def test_incomplete_saved_result_stops_without_next_request(self):
        calls, _, value = self.run_fake_recovery(incomplete=True)
        self.assertEqual(calls, ['b'])
        self.assertEqual(value['state'], 'halted_incomplete_observation_no_retry')

    def test_durable_timed_gate_reads_no_secret_and_starts_no_child_while_waiting(self):
        calls, _, value = self.run_fake_recovery(wait=True)
        self.assertEqual(calls, ['b', 'c'])
        self.assertEqual(value['state'], 'complete_advisories_agent_judgements_pending')

    def test_registered_installer_defers_only_new_entries_without_auth_probe(self):
        source = (ROOT.parents[1] / 'scripts/install-launchd-tasks.sh').read_text()
        embedded = source.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        function = next(node for node in ast.parse(embedded).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'install_task')
        ensure, command = unittest.mock.Mock(), unittest.mock.Mock()
        namespace = {'Any': object, 'ensure_op_service_account': ensure,
                     'install_command': command, 'install_plist': unittest.mock.Mock()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<canonical install_task>', 'exec'), namespace)
        proposals = json.loads((ROOT / 'registry-proposal.json').read_text())
        with patch('sys.stdout', new=io.StringIO()):
            for task in proposals:
                self.assertFalse(task['requiresOpServiceAccount'])
                self.assertTrue(task['runtimeRequiresOpServiceAccount'])
                self.assertEqual(task['credentialValidationPolicy'], 'deferred-runtime-single-read-after-pinned-not-before')
                namespace['install_task'](task, False)
        ensure.assert_not_called()
        self.assertEqual(command.call_count, 2)


if __name__ == '__main__':
    unittest.main()
