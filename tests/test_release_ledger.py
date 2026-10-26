import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('ledger',Path(__file__).resolve().parents[1]/'media-quality/video-review/build_release_ledger.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class ReadinessTests(unittest.TestCase):
    def evidence(self):
        return ({'outputSha256':'current'},{'sha256':'current','issues':[]},
            {'sourceSha256':'current','advisory':{'observed_complete':True,'issues':[],'uncertainties':[]}},
            {'sourceSha256':'current','status':'ready','reason':'Reviewed exact final evidence','evidence':['saved-review']})

    def test_completed_checks_without_agent_judgement_are_not_release_approval(self):
        p,t,rw,d=self.evidence();self.assertTrue(r.ready(p,t,rw,d));self.assertFalse(r.ready(p,t,rw,{}))

    def test_old_hash_judgement_cannot_approve_new_export(self):
        p,t,rw,d=self.evidence();d['sourceSha256']='previous';self.assertFalse(r.ready(p,t,rw,d))

    def test_findings_and_uncertainties_require_explicit_disposition(self):
        p,t,rw,d=self.evidence();rw['advisory']['issues']=[{'issue':'unreadable URL'}]
        self.assertFalse(r.ready(p,t,rw,d));d['resolvedAdvisoryIssues']={'0':'Native crop proves it is readable; evidence retained.'}
        self.assertTrue(r.ready(p,t,rw,d));rw['advisory']['uncertainties']=['Did not hear ending'];self.assertFalse(r.ready(p,t,rw,d))

if __name__=='__main__':unittest.main()
