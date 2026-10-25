import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock

from reasoning import Reasoner, source_blocks, validate, parse_response
from decision_fixtures import build_cases

class DecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.packages, self.expected = build_cases()
        self.decisions = []
        for p in self.packages:
            e = self.expected[p['packageId']]
            action = e['action']
            b = next(b for b in source_blocks(p) if all(r in b['text'] for r in e['evidenceRefs']))
            self.decisions.append({**e['facts'], 'packageId': p['packageId'], 'sourceBlockId': b['id'],
                'action': action, 'evidenceRefs': e['evidenceRefs'], 'reason': 'Current case facts support ' + action + ' using ' + ', '.join(e['evidenceRefs']),
                'assessment': {'sameInvoiceAlreadyPaid': action == 'reject_duplicate', 'materialConflict': action == 'open_exception', 'pendingVerification': action == 'hold_invoice', 'exceedsAuthority': action == 'request_approval', 'validAndReconciled': action != 'open_exception'}})
        self.cache = {}
        def key(policy, package):
            return (policy, json.dumps({k:v for k,v in package.items() if k != 'packageId'}, sort_keys=True))
        self.reasoner = Reasoner('test-key', 'test-model', lambda policy,p: self.cache.get(key(policy,p)), lambda policy,p,d: self.cache.update({key(policy,p):copy.deepcopy(d)}))

    def test_five_actions_are_valid(self):
        self.assertEqual({d['action'] for d in self.decisions}, {'settle_invoice','request_approval','hold_invoice','reject_duplicate','open_exception'})
        for d,p in zip(self.decisions,self.packages):
            self.assertIsNone(validate(d,p))

    def test_cross_paragraph_refs_rejected(self):
        d=copy.deepcopy(self.decisions[0]); d['evidenceRefs'][0]='[cover-01]'
        self.assertIsNotNone(validate(d,self.packages[0]))

    def test_wrong_package_rejected(self):
        d=copy.deepcopy(self.decisions[0]); d['packageId']='other'
        self.assertIsNotNone(validate(d,self.packages[0]))

    def test_repeated_evidence_rejected(self):
        d=copy.deepcopy(self.decisions[0]); d['evidenceRefs'][1]=d['evidenceRefs'][0]
        self.assertIsNotNone(validate(d,self.packages[0]))

    def test_boolean_amount_rejected(self):
        d=copy.deepcopy(self.decisions[0]); d['amountMinor']=True
        self.assertIsNotNone(validate(d,self.packages[0]))

    def test_contradictory_settlement_rejected(self):
        d=copy.deepcopy(self.decisions[0]); d['action']='settle_invoice'; d['assessment']['sameInvoiceAlreadyPaid']=True
        self.assertIsNotNone(validate(d,self.packages[0]))

    def test_multipart_answer_and_thought(self):
        answer=json.dumps({'decisions':self.decisions})
        response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'thought':True,'text':'not JSON'}, {'text':answer[:70]}, {'text':answer[70:]}]}}]}
        self.assertEqual(parse_response(response),self.decisions)

    def test_fenced_json(self):
        text='```json\n'+json.dumps({'decisions':self.decisions})+'\n```'
        self.assertEqual(parse_response({'candidates':[{'content':{'parts':[{'text':text}]}}]}),self.decisions)

    def test_damaged_batch_recovers_complete_decisions(self):
        text='{"decisions":['+json.dumps(self.decisions[0])+json.dumps(self.decisions[1])+', {"packageId":"cutoff'
        actual=parse_response({'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':text}]}}]})
        self.assertEqual(actual,self.decisions[:2])

    def test_incomplete_single_decision_not_fabricated(self):
        with self.assertRaises(ValueError):
            parse_response({'candidates':[{'content':{'parts':[{'text':'{"decisions":[{"packageId":"cutoff'}]}}]})

    async def test_cache_reuses_decision_with_new_package_namespace(self):
        async def call(policy, packages, feedback=None, review=False):
            return [d for d in self.decisions if d['packageId'] in {p['packageId'] for p in packages}]
        self.reasoner.call=AsyncMock(side_effect=call)
        first=await self.reasoner.decide('p1',self.packages)
        self.assertEqual(len(first),10)
        self.assertEqual(self.reasoner.call.await_count,2) # one batch plus batched settlement review
        changed=copy.deepcopy(self.packages)
        for p in changed: p['packageId']='new-'+p['packageId']
        again=await self.reasoner.decide('p1',changed)
        self.assertEqual(self.reasoner.call.await_count,2)
        for p in changed:
            self.assertEqual(again[p['packageId']]['packageId'],p['packageId'])

    async def test_only_failed_package_is_retried(self):
        async def call(policy, packages, feedback=None, review=False):
            if not feedback and not review:
                return self.decisions[1:]
            return [d for d in self.decisions if d['packageId'] in {p['packageId'] for p in packages}]
        self.reasoner.call=AsyncMock(side_effect=call)
        out=await self.reasoner.decide('p1',self.packages)
        self.assertEqual(len(out),10)
        self.assertEqual(len(self.reasoner.call.call_args_list[1].args[1]),1)
        self.assertEqual(len(self.cache),10)

    async def test_provider_failure_not_cached_or_settled(self):
        self.reasoner.call=AsyncMock(side_effect=ValueError('bad JSON'))
        out=await self.reasoner.decide('p1',self.packages[:1])
        self.assertEqual(out[self.packages[0]['packageId']]['action'],'open_exception')
        self.assertFalse(self.cache)

    async def test_reviewer_can_block_settlement(self):
        i=next(i for i,d in enumerate(self.decisions) if d['action']=='settle_invoice')
        d=copy.deepcopy(self.decisions[i]); d['action']='hold_invoice'; d['assessment']['pendingVerification']=True
        self.reasoner.call=AsyncMock(side_effect=[self.decisions,[d] + [x for x in self.decisions if x['action']=='settle_invoice' and x['packageId'] != d['packageId']]])
        out=await self.reasoner.decide('p1',self.packages)
        self.assertEqual(out[d['packageId']]['action'],'hold_invoice')

    async def test_concurrent_batches_coalesce_semantic_cache(self):
        async def call(policy, packages, feedback=None, review=False):
            await asyncio.sleep(0)
            return [d for d in self.decisions if d['packageId'] in {p['packageId'] for p in packages}]
        self.reasoner.call=AsyncMock(side_effect=call)
        await asyncio.gather(self.reasoner.decide('p1',self.packages),self.reasoner.decide('p1',self.packages))
        self.assertEqual(self.reasoner.call.await_count,2)

if __name__=='__main__': unittest.main()
