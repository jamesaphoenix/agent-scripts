import importlib.util
import json
from pathlib import Path
import fcntl
import tempfile
import unittest
from unittest.mock import MagicMock

BASE = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci/retention'
spec = importlib.util.spec_from_file_location('image_retention', BASE / 'jud-image-retention.py')
ret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ret)

NOW = 2_000_000_000.0
DAY = 86400
API = 'europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/api'
OTHER = 'redis'


def stamp(age_days):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(NOW - age_days * DAY, timezone.utc).strftime('%Y-%m-%d %H:%M:%S +0000 GMT')


def row(image_id, repo, tag='<none>', digest='<none>', size='1GB', age=10):
    return {'ID': 'sha256:' + image_id * 64, 'Repository': repo, 'Tag': tag, 'Digest': digest, 'Size': size, 'CreatedAt': stamp(age)}


def release(name, refs, age=10):
    return {'path': '/srv/jud/octospark-staging/releases/' + name, 'mtime': NOW - age * DAY, 'refs': refs}


def digest(char):
    return 'sha256:' + char * 64


def snapshot(releases, current, healthy, images, containers=None, state='ok'):
    return {
        'images': images, 'containers': containers or [],
        'projects': {'octospark-staging': {'state': state, 'current': {'release': current, 'refs': []},
                                           'healthy': healthy, 'releases': releases}},
    }


CONFIG = ret.merge_config({'projects': ['octospark-staging'], 'max_images': 0, 'max_release_dirs': 0})


def removed_ids(result):
    return {i['id'][7:8] for i in result['images_eligible']}


class Helpers(unittest.TestCase):
    def test_parse_size_and_created(self):
        self.assertEqual(ret.parse_size('2.11GB'), 2_110_000_000)
        self.assertEqual(ret.parse_size('0B'), 0)
        self.assertEqual(ret.parse_size('N/A'), 0)
        self.assertEqual(ret.parse_created('2026-11-11 14:16:27 +0000 GMT'), 1794406587.0)

    def test_repository_strips_tag_and_digest_but_not_registry_port(self):
        self.assertEqual(ret.repository(API + ':v1'), API)
        self.assertEqual(ret.repository('localhost:5000/x@sha256:ab'), 'localhost:5000/x')
        self.assertEqual(ret.repository('redis:8'), 'redis')

    def test_compose_refs_reads_json_and_yaml(self):
        self.assertEqual(ret.compose_refs('{"services":{"a":{"image":"x@sha256:1"},"b":{}}}'), {'x@sha256:1'})
        self.assertEqual(ret.compose_refs('services:\n  a:\n    image: "y:2" # c\n'), {'y:2'})


class Plan(unittest.TestCase):
    def releases(self):
        names = {c: API + '@' + digest(c) for c in 'abcde'}
        rel = [release(c, [names[c]], age=10 + i) for i, c in enumerate('abcde')]
        images = [row(c, API, digest=digest(c)) for c in 'abcdef6']  # f, 6 are in no release
        return names, rel, images

    def test_keeps_current_and_two_most_recent_healthy_and_removes_the_rest(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        healthy = [{'path': paths[c]} for c in 'edcb']  # oldest first, newest last
        result = ret.plan(snapshot(rel, paths['a'], healthy, images), CONFIG, NOW)
        self.assertEqual(result['projects']['octospark-staging']['kept'].__len__(), 3)
        self.assertEqual(sorted(p.rsplit('/', 1)[1] for p in result['release_dirs']), ['d', 'e'])
        # a (current), b, c (two newest healthy) are protected; d, e, f, 6 are not.
        self.assertEqual(removed_ids(result), {'d', 'e', 'f', '6'})

    def test_no_healthy_history_holds_the_whole_product(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        result = ret.plan(snapshot(rel, paths['a'], None, images), CONFIG, NOW)
        self.assertEqual(result['images_eligible'], [])
        self.assertEqual(result['release_dirs'], [])
        self.assertIn('octospark', result['held_products'])

    def test_unreadable_project_fails_closed(self):
        _, rel, images = self.releases()
        result = ret.plan(snapshot([], None, None, images, state='unreadable'), CONFIG, NOW)
        self.assertEqual(result['images_eligible'], [])

    def test_absent_project_does_not_hold_but_protects_nothing_extra(self):
        _, _, images = self.releases()
        result = ret.plan(snapshot([], None, None, images, state='absent'), CONFIG, NOW)
        self.assertEqual(len(result['images_eligible']), 7)

    def test_image_used_by_stopped_container_is_never_removed(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        healthy = [{'path': paths[c]} for c in 'cb']
        containers = [{'Names': 'old', 'Image': API + '@' + digest('f'), 'State': 'exited'},
                      {'Names': 'byid', 'Image': '6' * 12, 'State': 'exited'}]
        result = ret.plan(snapshot(rel, paths['a'], healthy, images, containers), CONFIG, NOW)
        self.assertNotIn('f', removed_ids(result))
        self.assertNotIn('6', removed_ids(result))
        self.assertIn('d', removed_ids(result))

    def test_resolved_container_image_ids_protect_by_exact_id(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        snap = snapshot(rel, paths['a'], [{'path': paths['b']}], images)
        snap['container_images'] = [{'name': '/x', 'image_id': 'sha256:' + 'f' * 64}]
        self.assertNotIn('f', removed_ids(ret.plan(snap, CONFIG, NOW)))

    def test_only_allow_listed_repositories_are_candidates(self):
        names, rel, images = self.releases()
        images += [row('9', OTHER, tag='8'), row('8', 'octospark-renderer', tag='ci-1'),
                   row('7', API, tag='x'), row('7', OTHER, tag='y')]  # mixed refs: not wholly allow-listed
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        result = ret.plan(snapshot(rel, paths['a'], [{'path': paths['b']}], images), CONFIG, NOW)
        self.assertTrue(removed_ids(result).isdisjoint({'9', '8', '7'}))

    def test_grace_period_and_dangling_age(self):
        names, rel, images = self.releases()
        images += [row('9', API, digest=digest('9'), age=1), row('8', '<none>', age=3), row('7', '<none>', age=20)]
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        result = ret.plan(snapshot(rel, paths['a'], [{'path': paths['b']}], images), CONFIG, NOW)
        ids = removed_ids(result)
        self.assertNotIn('9', ids)
        self.assertNotIn('8', ids)
        self.assertIn('7', ids)

    def test_young_release_directory_and_mounted_directory_are_kept(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        rel[3]['mtime'] = NOW - 3600            # d is young
        containers = [{'Names': 'm', 'Image': 'redis', 'Mounts': paths['e'] + '/config.json'}]
        result = ret.plan(snapshot(rel, paths['a'], [{'path': paths['b']}], images, containers), CONFIG, NOW)
        self.assertNotIn(paths['d'], result['release_dirs'])
        self.assertNotIn(paths['e'], result['release_dirs'])
        self.assertNotIn('d', removed_ids(result))      # its image stays while its release stays

    def test_caps_apply_per_run_but_eligible_total_is_reported(self):
        names, rel, images = self.releases()
        paths = {r['path'].rsplit('/', 1)[1]: r['path'] for r in rel}
        config = ret.merge_config({'projects': ['octospark-staging'], 'max_images': 2, 'max_release_dirs': 1})
        result = ret.plan(snapshot(rel, paths['a'], [{'path': paths['b']}], images), config, NOW)
        self.assertEqual(len(result['images']), 2)
        self.assertGreater(len(result['images_eligible']), 2)
        self.assertEqual(len(result['release_dirs']), 1)
        # A release directory not removed this run keeps protecting its image.
        kept_by_cap = [p for p in result['release_dirs_eligible'] if p not in result['release_dirs']]
        self.assertTrue(kept_by_cap)

    def test_history_accepts_dict_keyed_by_project(self):
        self.assertEqual(ret.history_entries({'octospark-staging': [{'path': 'x'}]}, 'octospark-staging'), [{'path': 'x'}])

    def test_report_ranks_unreferenced_by_size_and_totals_by_repository(self):
        images = [row('a', API, tag='1', size='3GB'), row('b', API, tag='2', size='1GB'), row('c', OTHER, tag='3', size='9GB')]
        snap = {'images': images, 'containers': [{'Names': 'r', 'Image': 'redis:3'}], 'projects': {},
                'build_cache': {'Size': '140.8GB'}}
        data = ret.report(snap, CONFIG, NOW)
        self.assertEqual([i['size'] for i in data['largest']], [3_000_000_000, 1_000_000_000])
        self.assertEqual(data['by_repository'][0][0], API)
        self.assertEqual(data['build_cache']['Size'], '140.8GB')


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'octospark-staging' / 'releases' / 'old').mkdir(parents=True)
        (self.root / 'octospark-staging' / 'releases' / 'old' / 'f').write_text('x')
        self.config = ret.merge_config({'root': str(self.root), 'projects': ['octospark-staging']})
        self.result = {'release_dirs': [str(self.root / 'octospark-staging/releases/old')],
                       'images': [{'id': 'sha256:' + 'a' * 64, 'refs': {API + ':1', API + '@' + digest('a')}}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_by_reference_and_never_forces(self):
        docker = MagicMock()
        out = ret.apply(self.result, self.config, docker)
        self.assertEqual(out['errors'], [])
        calls = [c.args for c in docker.run.call_args_list]
        self.assertEqual(len(calls), 2)
        for args in calls:
            self.assertEqual(args[:2], ('image', 'rm'))
            self.assertNotIn('-f', args)
            self.assertNotIn('--force', args)
        self.assertFalse((self.root / 'octospark-staging/releases/old').exists())

    def test_busy_deploy_lock_skips_everything(self):
        with (self.root / 'octospark-staging' / 'deploy.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            docker = MagicMock()
            out = ret.apply(self.result, self.config, docker)
        self.assertIn('lock busy', out['skipped'])
        docker.run.assert_not_called()
        self.assertTrue((self.root / 'octospark-staging/releases/old').exists())

    def test_busy_host_lock_skips_everything(self):
        with (self.root / 'deploy.host.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            out = ret.apply(self.result, self.config, MagicMock())
        self.assertIn('deploy.host.lock', out['skipped'])

    def test_failed_removal_is_recorded_not_forced(self):
        docker = MagicMock()
        docker.run.side_effect = RuntimeError('in use')
        out = ret.apply(self.result, self.config, docker)
        self.assertEqual(len(out['errors']), 1)

    def test_release_path_outside_root_is_refused(self):
        self.result['release_dirs'] = ['/etc']
        out = ret.apply(self.result, self.config, MagicMock())
        self.assertIn('unsafe', out['errors'][0])


class Cli(unittest.TestCase):
    def test_snapshot_mode_never_applies(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json') as handle:
            json.dump({'images': [], 'containers': [], 'projects': {}}, handle)
            handle.flush()
            with self.assertRaises(SystemExit):
                ret.main(['--from-snapshot', handle.name, '--apply'])

    def test_snapshot_dry_run_exits_zero(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json') as handle:
            json.dump({'images': [row('a', API, tag='1')], 'containers': [], 'projects': {}, 'now': NOW}, handle)
            handle.flush()
            self.assertEqual(ret.main(['--from-snapshot', handle.name]), 0)


class ReadProject(unittest.TestCase):
    def test_reads_current_history_and_release_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'octospark-staging'
            rel = project / 'releases' / 'r1'
            rel.mkdir(parents=True)
            (project / 'current.json').write_text(json.dumps({'release': str(rel), 'images': {'API_IMAGE': API + '@' + digest('a')}}))
            (project / 'healthy-releases.json').write_text('[]')
            (rel / 'release.json').write_text(json.dumps({'images': {'API_IMAGE': API + '@' + digest('a')}}))
            (rel / 'compose.json').write_text(json.dumps({'services': {'x': {'image': 'redis:8'}}}))
            data = ret.read_project(project)
            self.assertEqual(data['state'], 'ok')
            self.assertEqual(sorted(data['releases'][0]['refs']), sorted([API + '@' + digest('a'), 'redis:8']))
            self.assertEqual(ret.read_project(Path(tmp) / 'nope')['state'], 'absent')
            (project / 'current.json').write_text('{bad')
            self.assertEqual(ret.read_project(project)['state'], 'unreadable')


if __name__ == '__main__':
    unittest.main()
