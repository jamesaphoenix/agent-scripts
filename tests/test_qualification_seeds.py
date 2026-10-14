from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from qualification_seeds import publish, import_batches
from qualify_inventory import create_database


class QualificationSeedTests(unittest.TestCase):
    def test_repeated_batches_and_alias_urls_never_reset_a_fetch_hold(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); db = create_database(root / 'candidates.sqlite')
            seed = {'name': 'Jane Smith', 'website': 'https://jane.example/'}
            publish(root, [seed], 'https://local-directory.example/tutors')
            self.assertEqual(import_batches(root, db)['importedCandidates'], 1)
            with db:
                db.execute("UPDATE candidates SET state='fetch_held',error='403'")
            publish(root, [dict(seed, website='http://www.jane.example/about')], 'https://other-directory.example/tutors')
            for _ in range(2): self.assertEqual(import_batches(root, db)['importedCandidates'], 1)
            self.assertEqual(db.execute('SELECT state,error FROM candidates').fetchone()[:], ('fetch_held', '403'))
            db.close()

    def test_invalid_seed_does_not_prevent_another_batch_and_private_routes_are_held(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); db = create_database(root / 'candidates.sqlite')
            source = 'https://local-directory.example/tutors'
            publish(root, [{'name': 'Private', 'website': 'https://bad.example/login'}], source)
            publish(root, [{'name': 'Jane Smith', 'website': 'https://jane.example/'}], source)
            result = import_batches(root, db)
            self.assertEqual(result, {'importedCandidates': 1, 'heldBatches': 1})
            db.close()

    def test_foreign_context_and_edits_are_held_without_partial_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); db = create_database(root / 'candidates.sqlite')
            good = {'name': 'Jane Smith', 'website': 'https://jane.example/'}
            bad = dict(good, siteEvidence=[{'url': 'https://another.example/about'}])
            publish(root, [good, bad], 'https://local-directory.example/tutors')
            self.assertEqual(import_batches(root, db)['importedCandidates'], 0)
            key = publish(root, [good], 'https://local-directory.example/tutors')
            path = root / 'website-seeds' / (key + '.json'); path.write_text(path.read_text() + ' ')
            self.assertEqual(import_batches(root, db)['heldBatches'], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0], 0)
            db.close()


if __name__ == '__main__': unittest.main()
