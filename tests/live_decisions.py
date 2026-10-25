"""Exercise the public service using synthetic invoices and a fresh principal."""
import json, os, sys, time, uuid
from pathlib import Path
import httpx
from decision_fixtures import build_cases

base = sys.argv[1].rstrip('/')
packages, expected = build_cases()
headers = {'Authorization': 'Bearer synthetic-test-' + uuid.uuid4().hex, 'A2A-Version': '1.0', 'Content-Type': 'application/a2a+json'}
body = {'message': {'messageId': uuid.uuid4().hex, 'role': 'ROLE_USER', 'parts': [{'mediaType': 'application/vnd.ga5.invoice-claim-batch+json', 'data': {'batchId': uuid.uuid4().hex, 'policyRevision': 'synthetic-v1', 'packages': packages}}]}}
start = time.monotonic()
with httpx.Client(timeout=120) as c:
    r = c.post(base + '/message:send', json=body, headers=headers)
    r.raise_for_status()
    task = r.json()['task']
    proposals = task['artifacts'][0]['parts'][0]['data']['proposals']
    checks = []
    for p in proposals:
        e = expected[p['packageId']]
        checks.append({'expected': e['action'], 'actual': p['action'], 'actionOK': p['action'] == e['action'], 'factsOK': p['facts'] == e['facts'], 'evidenceOK': set(p['evidenceRefs']) == set(e['evidenceRefs']), 'rationaleOK': 60 <= len(p['rationale']) <= 1500 and p['action'] in p['rationale'] and all(ref in p['rationale'] for ref in e['evidenceRefs'])})
    print(json.dumps({'seconds': round(time.monotonic()-start, 2), 'count': len(proposals), 'checks': checks}, indent=2), flush=True)
    if len(sys.argv)>2:
        Path(sys.argv[2]).write_text(json.dumps({'request': body, 'task': task, 'expected': expected, 'checks': checks}, indent=2))
    result = {'message': {'messageId': uuid.uuid4().hex, 'taskId': task['id'], 'contextId': task['contextId'], 'role': 'ROLE_USER', 'parts': [{'mediaType': 'application/vnd.ga5.invoice-action-results+json', 'data': {'batchId': body['message']['parts'][0]['data']['batchId'], 'results': [{'packageId': p['packageId'], 'actionId': p['actionId'], 'action': p['action'], 'outcome': 'REJECTED', 'receiptNonce': uuid.uuid4().hex} for p in proposals]}}]}}
    rr = c.post(base + '/message:send', json=result, headers=headers)
    rr.raise_for_status()
    completed = rr.json()['task']
    assert completed['status']['state'] == 'TASK_STATE_COMPLETED'
    assert completed['artifacts'][1]['parts'][0]['data']['executions'] == []
    assert c.post(base + '/message:send', json=result, headers=headers).json()['task'] == completed
    print('Rejected-result lifecycle and terminal replay passed.', flush=True)
