import unittest
from datetime import datetime, timezone, timedelta
from policy import inference_limit


class RenderPriorityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.resources = dict(pressureSampledAt=self.now.isoformat(), admissionPaused=False, criticalMemoryPressure=False, memoryPressure=False, cpuPressure=False, inUse=0, waiting=0, activeRenders=0)

    def test_executing_renders_get_priority(self):
        self.assertEqual(inference_limit(self.resources, self.now)[0], 2)
        self.resources['activeRenders'] = 1
        self.assertEqual(inference_limit(self.resources, self.now)[0], 1)
        self.resources['cpuPressure'] = True
        self.assertEqual(inference_limit(self.resources, self.now)[0], 0)

    def test_idle_temporal_pollers_do_not_starve_inference(self):
        self.resources.update(inUse=2, waiting=2)
        self.assertEqual(inference_limit(self.resources, self.now)[0], 2)

    def test_memory_safety_and_stale_resources_fail_closed(self):
        for key in ['admissionPaused', 'criticalMemoryPressure', 'memoryPressure']:
            with self.subTest(key=key):
                self.assertEqual(inference_limit(dict(self.resources, **{key: True}), self.now)[0], 0)
        self.assertEqual(inference_limit(self.resources, self.now + timedelta(seconds=16))[0], 0)
        self.assertEqual(inference_limit({}, self.now)[0], 0)

    def test_qwen_slowdown_only_changes_inference_admission(self):
        self.assertEqual(inference_limit(self.resources, self.now, slowdown=True)[0], 1)
        self.assertEqual(self.resources['inUse'], 0)
        self.assertFalse(self.resources['admissionPaused'])


if __name__ == '__main__':
    unittest.main()
