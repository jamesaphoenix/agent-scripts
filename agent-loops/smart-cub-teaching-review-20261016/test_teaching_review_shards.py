"""Checkpoint migration invariants. No media, provider, host or launchd actions."""
import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile
import json


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shard = load('shard_test', 'teaching-review-shard.py')
migration = load('migration_test', 'migrate-teaching-review-shards.py')


class ShardOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'skillKey': f'english.grammar.lesson-{n:03d}'} for n in range(144)]
        self.completed = {
            f"{self.rows[n]['skillKey']}:{aspect}": {
                'binding': {'output_sha256': f'{n:064x}', 'receipt_sha256': 'e' * 64},
                'request': f'out/review/{n}/{aspect}/request.json', 'request_sha256': 'c' * 64,
                'observedAt': '2026-10-16T19:00:00Z', 'publicationApproval': False}
            for n in range(31) for aspect in ['wide', 'tall']}

    def test_complete_curriculum_is_disjoint_with_both_aspects_on_one_owner(self):
        configs, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        keys = [set(c['ownedKeys']) for c in configs]
        self.assertFalse(keys[0] & keys[1])
        self.assertEqual([len(k) for k in keys], [144, 144])
        self.assertEqual(len(keys[0] | keys[1]), 288)
        for row in self.rows:
            owners = [i for i, keys_for_owner in enumerate(keys)
                      if f"{row['skillKey']}:wide" in keys_for_owner]
            self.assertEqual(len(owners), 1)
            self.assertIn(f"{row['skillKey']}:tall", keys[owners[0]])
        self.assertEqual({**portions[0], **portions[1]}, self.completed)
        self.assertFalse(set(portions[0]) & set(portions[1]))

    def test_partial_completion_and_new_readiness_do_not_change_ownership(self):
        before, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        incomplete = copy.deepcopy(self.completed)
        incomplete.pop(next(iter(incomplete)))
        after, _ = shard.partition(self.rows, 'a' * 64, incomplete)
        self.assertEqual(before, after)
        portions[0][next(iter(portions[0]))]['binding']['output_sha256'] = 'changed'
        self.assertNotEqual({**portions[0], **portions[1]}, self.completed)
        self.assertNotIn('changed', str(self.completed))

    def test_unknown_prior_target_or_duplicate_curriculum_never_silently_disappears(self):
        for rows, completed in [
                (self.rows, {**self.completed, 'english.unknown:wide': {}}),
                (self.rows + [self.rows[0]], self.completed)]:
            with self.assertRaises(ValueError):
                shard.partition(rows, 'a' * 64, completed)

    def test_reordered_or_changed_manifest_requires_a_new_explicit_migration(self):
        configs, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        state = migration.checkpoint({}, configs[0], portions[0], {'review': 'r'}, 'w', 'g', 'p', 'proof')
        for config in [shard.configuration(list(reversed(self.rows)), 'a' * 64, 0),
                       shard.configuration(self.rows, 'b' * 64, 0)]:
            with self.assertRaises(ValueError):
                shard.validate_checkpoint(state, config, {'review': 'r'}, 'w', 'g')

    def test_wrong_owner_or_changed_implementation_cannot_resume(self):
        configs, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        state = migration.checkpoint({}, configs[0], portions[0], {'review': 'r'}, 'w', 'g', 'p', 'proof')
        shard.validate_checkpoint(state, configs[0], {'review': 'r'}, 'w', 'g')
        for field in ['dispatcherSha256', 'admissionSha256', 'baseDispatcherSha256', 'protectedSourceHashes']:
            bad = copy.deepcopy(state)
            bad[field] = 'changed'
            with self.assertRaises(ValueError):
                shard.validate_checkpoint(bad, configs[0], {'review': 'r'}, 'w', 'g')
        state['completedOutputs'].update(portions[1])
        with self.assertRaises(ValueError):
            shard.validate_checkpoint(state, configs[0], {'review': 'r'}, 'w', 'g')

    def test_dispatch_cannot_start_before_final_migration_proof_or_after_tampering(self):
        configs, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            proof_path, status = folder / 'proof.json', folder / 'status.json'
            state = migration.checkpoint({}, configs[0], portions[0], {}, 'w', 'g', 'parent', proof_path)
            status.write_text(json.dumps(state))
            with self.assertRaises(FileNotFoundError):
                shard.validate_migration(state, status, 'w', 'g')
            proof = {'contract': 'teaching-two-shard-migration/v1', 'shardWrapperSha256': 'w',
                     'admissionSha256': 'g', 'parentCheckpointSha256': 'parent',
                     'partitionDisjointAndExactUnion': True, 'allCompletedRequestsSettled': True,
                     'noUnresolvedTeachingPaidIntent': True,
                     'newCheckpoints': [{'path': str(status), 'sha256': shard.sha(status)}]}
            proof_path.write_text(json.dumps(proof))
            checksum = shard.validate_migration(state, status, 'w', 'g')
            status.write_text(status.read_text() + ' ')
            with self.assertRaises(ValueError):
                shard.validate_migration(state, status, 'w', 'g')
            state.update(phase='running', migrationProofSha256=checksum)
            shard.validate_migration(state, status, 'w', 'g')
            proof_path.write_text(proof_path.read_text() + ' ')
            with self.assertRaises(ValueError):
                shard.validate_migration(state, status, 'w', 'g')

    def test_new_checkpoints_never_approve_or_retry_and_preserve_parent_binding(self):
        configs, portions = shard.partition(self.rows, 'a' * 64, self.completed)
        states = [migration.checkpoint({}, c, p, {}, 'w', 'g', 'original', 'proof')
                  for c, p in zip(configs, portions)]
        self.assertEqual({**states[0]['completedOutputs'], **states[1]['completedOutputs']}, self.completed)
        for state in states:
            self.assertFalse(state['publicationApproval'])
            self.assertFalse(state['automaticRetry'])
            self.assertEqual(state['parentCheckpointSha256'], 'original')
            self.assertEqual(state['phase'], 'ready')


class SettledObservationTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'request_sha256': 'a' * 64, 'binding': {'output_sha256': 'b' * 64}}
        self.job = {'state': 'completed_advisory', 'budget_state': 'settled_usage_tariff_estimate_rounded_up',
                    'version_key': 'key', 'version': {'context_sha256': 'context', 'output_sha256': 'b' * 64}}
        self.reservation = {'state': 'settled'}
        self.context = {'approved_body_script': 'A complete approved lesson.', 'source_row': {'hash': 'original'}}
        self.media = type('FakeMedia', (), {'current_review': staticmethod(lambda job, key: key == 'key')})

    def check(self, **changes):
        values = dict(entry=self.entry, job=self.job, reservation=self.reservation,
                      context=self.context, current_context=self.context, ready_binding=self.entry['binding'],
                      request_sha='a' * 64, context_digest='context', review_module=self.media)
        values.update(changes)
        migration.settled_observation(**values)

    def test_real_settlement_label_is_accepted_but_submission_and_unresolved_holds_are_not(self):
        self.check()
        for state in ['submitted_unknown', 'uploading', 'response_saved', 'reserved']:
            job = copy.deepcopy(self.job)
            job['state'] = state
            with self.assertRaises(ValueError):
                self.check(job=job)
        with self.assertRaises(ValueError):
            self.check(reservation={'state': 'reserved'})
        job = copy.deepcopy(self.job)
        job['budget_state'] = 'settled'
        with self.assertRaises(ValueError):
            self.check(job=job)

    def test_changed_source_context_output_or_request_blocks_migration(self):
        for change in [dict(request_sha='f' * 64), dict(ready_binding={'output_sha256': 'f' * 64}),
                       dict(current_context={**self.context, 'approved_body_script': 'Changed lesson.'}),
                       dict(context_digest='changed')]:
            with self.assertRaises(ValueError):
                self.check(**change)
        job = copy.deepcopy(self.job)
        job['version']['output_sha256'] = 'c' * 64
        with self.assertRaises(ValueError):
            self.check(job=job)


if __name__ == '__main__':
    unittest.main()
