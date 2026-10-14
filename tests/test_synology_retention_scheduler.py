import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('nas_installer', Path(__file__).resolve().parents[1] / 'agent-loops/synology-retention/install-dsm.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SchedulerTests(unittest.TestCase):
    def test_create_then_reinstall_keeps_one_nonroot_hourly_task(self):
        tasks = []
        calls = []

        def api(method, **params):
            calls.append(method)
            if method == 'list':
                return {'tasks': tasks}
            if method == 'get':
                return tasks[0] if params['id'] != -1 else {'schedule': {}}
            if method == 'create':
                tasks.append(dict(params, id=7))
            if method == 'set':
                tasks[0].update(params)
            return {}

        for _ in range(2):
            result = installer.install(api)
            self.assertEqual(result['owner'], 'mydocker')
            self.assertEqual(result['extra']['script'], installer.COMMAND)
            self.assertTrue(result['enable'])
            self.assertEqual(result['schedule']['repeat_hour'], 1)
            self.assertEqual(result['schedule']['minute'], 23)
            self.assertEqual(result['schedule']['hour'], 0)
            self.assertEqual(result['schedule']['last_work_hour'], 23)
            self.assertEqual(result['schedule']['week_day'], '0,1,2,3,4,5,6')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(calls.count('create'), 1)
        self.assertEqual(calls.count('set'), 1)

    def test_conflicting_owner_or_duplicate_tasks_cannot_be_overwritten(self):
        for rows in ([{'name': installer.NAME, 'type': 'script', 'owner': 'root'}],
                     [{'name': installer.NAME}] * 2):
            with self.assertRaises(RuntimeError):
                installer.install(lambda method, **params: {'tasks': rows})

    def test_conflicting_command_cannot_be_overwritten(self):
        def api(method, **params):
            if method == 'list':
                return {'tasks': [{'name': installer.NAME, 'type': 'script', 'owner': 'mydocker', 'id': 8}]}
            if method == 'get':
                return {'extra': {'script': 'some unrelated task'}}
            self.fail('Unexpected mutation: ' + method)
        with self.assertRaises(RuntimeError):
            installer.install(api)


if __name__ == '__main__':
    unittest.main()
