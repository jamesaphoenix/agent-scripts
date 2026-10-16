import sys
import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import request_key
from endpoint_probe import plan, business_plan, nominations, effective_plan, trial_exposure
import endpoint_probe
from discovery import atomic_json, file_hash

SCOPE = {'subjects': ['english','maths'], 'includeElevenPlus': True, 'includePrimaryTutors': True}
TOWNS = [{'name': name, 'coordinate':'51.6,-0.7'} for name in ['High Wycombe','Aylesbury','Milton Keynes']]


def response(items):
    return {'tasks':[{'result':[{'items':items}]}]}


class EndpointProbeTests(unittest.TestCase):
    def test_fixed_plan_is_within_fifty_cents_and_uses_distinct_endpoints(self):
        rows=plan(TOWNS)
        self.assertEqual(len(rows),10)
        self.assertEqual(sum(x['maximumCents'] for x in rows),50)
        self.assertEqual({x['lane'] for x in rows},{'local_finder','bing','html_terms'})
        self.assertEqual(len({request_key(x['lane'],x['payload']) for x in rows}),10)
        self.assertTrue(all('se_domain' not in row['payload'] for row in rows))
        with self.assertRaises(StopIteration): plan(TOWNS[:1])

    def test_local_finder_repair_retains_the_existing_plan_and_trial_spend(self):
        original=plan(TOWNS)
        original[0]['payload']['se_domain']='google.co.uk'
        repaired=effective_plan(original)
        self.assertIn('se_domain',original[0]['payload'])
        self.assertNotIn('se_domain',repaired[0]['payload'])
        cache=MagicMock()
        old=request_key('local_finder',original[0]['payload'])
        new=request_key('local_finder',repaired[0]['payload'])
        cache.get.side_effect=lambda key: ({'status':'failed','cost_cents':0} if key==old else
            {'status':'uncertain','cost_cents':0,'reservation':3} if key==new else None)
        self.assertEqual(trial_exposure(cache,original),3)

    def test_local_results_require_real_niche_and_area_hints_and_deduplicate(self):
        data=response([{'url':'https://janetutor.co.uk/','title':'Jane Maths Tutor Aylesbury'},
            {'url':'https://janetutor.co.uk/','title':'Read more'},
            {'url':'https://remote-tutor.co.uk/','title':'Maths tutor in Glasgow'},
            {'url':'https://kumon.co.uk/','title':'Kumon Aylesbury'},
            {'url':'https://piano.co.uk/','title':'Piano tutor Aylesbury'}])
        accepted,held,_=nominations(data,'bing',[t['name'] for t in TOWNS],SCOPE,set())
        self.assertEqual([x['name'] for x in accepted],['Jane Maths Tutor Aylesbury'])
        self.assertEqual(held['chain'],1)
        self.assertEqual(held['no_local_hint'],1)
        self.assertEqual(held['music_only_hint'],1)

    def test_html_search_terms_do_not_prove_the_returned_domain_is_a_local_tutor(self):
        data=response([{'domain':'janeslessons.co.uk','title':'Jane Smith'}])
        accepted,_,_=nominations(data,'html_terms',[t['name'] for t in TOWNS],SCOPE,set())
        self.assertEqual(accepted,[])
        data['tasks'][0]['result'][0]['items'][0]['description']='English tutor Buckinghamshire'
        self.assertEqual(nominations(data,'html_terms',[t['name'] for t in TOWNS],SCOPE,set())[0],
                         [{'name':'Jane Smith','website':'https://janeslessons.co.uk/','sourceUrl':'https://janeslessons.co.uk/'}])
        self.assertFalse(nominations(data,'html_terms',[t['name'] for t in TOWNS],SCOPE,{'janeslessons.co.uk'})[0])

    def test_raw_ip_results_are_held_as_unsupported_business_identities(self):
        data=response([{'domain':'87.120.9.184','title':'Maths tutor Buckinghamshire'}])
        accepted,held,_=nominations(data,'html_terms',[t['name'] for t in TOWNS],SCOPE,set())
        self.assertEqual(accepted,[])
        self.assertEqual(held['raw_ip_address'],1)

    def test_business_supplement_uses_different_categories_with_bounded_reservations(self):
        rows=business_plan(TOWNS)
        self.assertEqual(len(rows),3)
        self.assertEqual(sum(x['maximumCents'] for x in rows),21)
        self.assertTrue(all('tutoring_service' not in x['payload']['categories'] for x in rows))

    def test_real_academic_listing_category_can_nominate_but_school_and_music_cannot(self):
        data=response([{'url':'https://janeslearning.co.uk/','title':'Jane Learning','category':'Education center','address':'Aylesbury'},
            {'url':'https://primary-school.co.uk/','title':'Primary school Aylesbury','category':'Primary school'},
            {'url':'https://music-school.co.uk/','title':'School Aylesbury','category':'Music school'}])
        accepted,held,_=nominations(data,'business',[t['name'] for t in TOWNS],SCOPE,set())
        self.assertEqual([x['name'] for x in accepted],['Jane Learning'])
        self.assertEqual(len(accepted),1)

    def test_branch_and_directory_bounds_are_preserved(self):
        data=response([{'domain':f'local-tutor-{i}.co.uk','title':'Maths Tutor Bucks'} for i in range(20)])
        accepted,held,_=nominations(data,'html_terms',[t['name'] for t in TOWNS],SCOPE,set())
        self.assertEqual(len(accepted),15)
        self.assertEqual(held['branch_limit'],5)
        data=response([{'url':'https://www.findtutors.co.uk/','title':'Maths tutors Aylesbury'}])
        self.assertFalse(nominations(data,'bing',[t['name'] for t in TOWNS],SCOPE,set())[0])

    def run_trial(self, deadline, receipt):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        base=Path(temporary.name);root=base/'probe';root.mkdir();campaign=base/'campaign';campaign.mkdir()
        bank=base/'bank';bank.mkdir();atomic_json(root/'plan.json',plan(TOWNS))
        atomic_json(root/'job.json',{'campaignRoot':str(campaign),'instruction':endpoint_probe.INSTRUCTION,
            'maximumCents':50,'planSha256':file_hash(root/'plan.json')})
        atomic_json(campaign/'runtime.json',{'phase':'interrupted'})
        job={'towns':TOWNS,'recoveryBankRoot':str(bank),'authorizedDeadline':'original deadline'}
        cache=MagicMock();cache.usage.return_value={'remainingCents':100};cache.get.return_value=receipt
        with patch.object(endpoint_probe,'load_job',return_value=(job,deadline)), \
                patch.object(endpoint_probe,'PaidCache',return_value=cache), \
                patch.object(endpoint_probe.spend_limit,'policy',return_value={}), \
                patch.object(endpoint_probe,'known_owners',return_value=set()), \
                patch.object(endpoint_probe,'load_scope',return_value=SCOPE), \
                patch.object(endpoint_probe,'loaded_source_sha',return_value='a'*40), \
                patch.object(endpoint_probe,'STOP',False), \
                patch.object(endpoint_probe.signal,'signal'), \
                patch.object(endpoint_probe,'provider_request',return_value={'tasks':[]}) as paid, \
                patch.object(endpoint_probe.subprocess,'run') as resume:
            endpoint_probe.run(root)
        return json.loads((root/'status.json').read_text()),paid.call_count,resume.call_count,json.loads((campaign/'runtime.json').read_text())

    def test_deadline_prevents_paid_admission_and_restart(self):
        status,calls,restarts,_=self.run_trial(time.time()-1,None)
        self.assertEqual(status['phase'],'deadline_or_stop')
        self.assertEqual((calls,restarts),(0,0))

    def test_account_failure_propagates_stop_without_resuming_the_paid_owner(self):
        status,calls,restarts,campaign=self.run_trial(time.time()+1000,
            {'status':'failed','error':'HTTP Error 401: Unauthorized'})
        self.assertEqual(status['phase'],'account_blocked')
        self.assertEqual((calls,restarts),(1,0))
        self.assertEqual(campaign['phase'],'account_blocked')

    def test_refused_reservation_is_a_budget_stop_without_an_uncertain_submission(self):
        status,calls,_,campaign=self.run_trial(time.time()+1000,None)
        self.assertEqual(status['phase'],'budget_reached')
        self.assertEqual(calls,1)
        self.assertNotIn('endpointProbeStop',campaign)


if __name__=='__main__':unittest.main()
