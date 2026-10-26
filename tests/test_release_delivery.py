import copy
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/'media-quality/video-review/prepare_release_delivery.py'
s=importlib.util.spec_from_file_location('delivery',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)


class DeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.manifest={'videos':[{'kind':'teaching','id':str(i),'aspect':'tall','sha256':str(i)} for i in range(578)]}
        self.ledger={'scope':{'selectedExports':578},'allSelectedReadyForSocial':True,
                     'budget':{'actual_cents':100,'reserved_cents':1000},
                     'videos':[{'identity':f'teaching|{i}|tall','sourceSha256':str(i),'readyForSocial':True} for i in range(578)]}

    def test_full_matching_scope_passes(self):
        self.assertEqual(len(d.validate(self.manifest,self.ledger)),578)

    def test_one_changed_export_blocks_delivery(self):
        self.manifest['videos'][99]['sha256']='new-render'
        with self.assertRaisesRegex(ValueError,'Stale'):d.validate(self.manifest,self.ledger)

    def test_dropped_item_or_duplicate_cannot_be_called_complete(self):
        for rows in [self.manifest['videos'][:-1],self.manifest['videos'][:-1]+[self.manifest['videos'][0]]]:
            with self.assertRaises(ValueError):d.validate({'videos':rows},self.ledger)

    def test_restrictions_and_holds_cannot_be_omitted(self):
        ledger=copy.deepcopy(self.ledger)
        ledger['videos'][0]['distributionRestrictions']=['Post before 30 October']
        with self.assertRaisesRegex(ValueError,'restriction'):d.validate(self.manifest,ledger)
        self.ledger['budget']['reserved_cents']=4901
        with self.assertRaisesRegex(ValueError,'Budget'):d.validate(self.manifest,self.ledger)

    def test_one_pending_decision_blocks_even_if_summary_claims_ready(self):
        self.ledger['videos'][5]['readyForSocial']=False
        with self.assertRaisesRegex(ValueError,'unapproved'):d.validate(self.manifest,self.ledger)

    def test_late_repair_blocks_previously_approved_generation(self):
        self.ledger['pendingRepairs']=[{'repair':'subject CTA relevance'}]
        with self.assertRaisesRegex(ValueError,'Pending targeted repairs'):
            d.validate(self.manifest,self.ledger)


if __name__=='__main__':unittest.main()
