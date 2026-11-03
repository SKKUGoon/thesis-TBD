"""Check frontend bootstrap APIs against real auth, RLS membership and signed cursors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fault_scenario import compose, sql
from system_scenario import Client


def run(base: str, token: str, output: Path) -> None:
    """Create synthetic identities/scope and write token-free verification evidence."""
    evidence: dict = {"synthetic_only": True, "checks": [], "status": "running"}
    try:
        client = Client(base, token)
        me = client.get('/me')
        assert me['platform_role'] == 'test_admin' and me['roles'] == []
        created = client.post('/test/scenarios', {'name': 'Frontend auth boundaries', 'seed': 29, 'hospital_code': 'SYN', 'warehouse_code': 'SYN', 'first_week_start': '2024-03-04', 'initial_history_days': 112, 'profile': 'smoke_v1'})
        scope = created['scenario_id']
        client.scope = scope
        client.wait_ready()
        assert {'approver', 'receiver', 'test_operator'} <= set(client.get('/me')['roles'])
        client.scope = None
        all_scenarios = client.all('/test/scenarios')
        assert scope in {s['id'] for s in all_scenarios}
        assert len(all_scenarios) == len({s['id'] for s in all_scenarios})
        first = client.get('/test/scenarios?limit=1')
        cursor = first['next_cursor']
        assert cursor
        second = client.get('/test/scenarios?'+urlencode({'limit': 1, 'cursor': cursor}))
        assert first['items'][0]['id'] != second['items'][0]['id']
        assert client.request('/test/scenarios?cursor=invalid')[0] == 400
        assert client.request('/test/scenarios?limit=0')[0] == 422
        evidence['checks'].append('verified_identity_roles_creator_list_and_signed_pagination')

        def identity(role: str) -> tuple[str, str]:
            subject = 'frontend-check-'+uuid4().hex
            identifier = sql(f"INSERT INTO app.principal(subject,kind,platform_role) VALUES('{subject}','human','{role}') RETURNING id").splitlines()[0]
            script = "import jwt,os,time;print(jwt.encode({'sub':'"+subject+"','iat':int(time.time()),'exp':int(time.time())+600,'iss':'paper-operations','aud':'paper-api'},os.environ['JWT_SECRET'],algorithm='HS256'))"
            return identifier, compose('exec','-T','api','python','-c',script)

        other_id, other_token = identity('test_admin')
        other = Client(base, other_token)
        assert other.get('/test/scenarios')['items'] == []
        assert other.request('/test/scenarios?'+urlencode({'cursor': cursor}))[0] == 400
        other.scope = scope
        assert other.request('/me')[0] == 404
        assert other.request('/test/scenarios/'+scope)[0] == 404
        evidence['checks'].append('other_principal_no_scenario_leak_cursor_reuse_or_scope_access')
        restricted_id, restricted_token = identity('none')
        sql(f"INSERT INTO app.membership(scope_id,principal_id,role) VALUES('{scope}','{restricted_id}','viewer'),('{scope}','{restricted_id}','test_operator')")
        viewer = Client(base, restricted_token, scope)
        assert set(viewer.get('/me')['roles']) == {'viewer', 'test_operator'}
        assert viewer.get('/items')['items']
        assert viewer.get('/test/scenarios/'+scope)['id'] == scope
        viewer.scope = None
        assert viewer.request('/test/scenarios')[0] == 403
        sql(f"DELETE FROM app.membership WHERE scope_id='{scope}' AND principal_id='{restricted_id}' AND role='test_operator'")
        viewer.scope = scope
        assert viewer.get('/me')['roles'] == ['viewer']
        assert viewer.request('/test/scenarios/'+scope)[0] == 403
        sql(f"UPDATE app.principal SET disabled=true WHERE id='{restricted_id}'")
        # Existing API maps a disabled/unknown authenticated subject to NOT_FOUND.
        assert viewer.request('/me')[0] == 404
        evidence['checks'].append('membership_rechecked_role_removal_and_disabled_identity')
        evidence['status'] = 'succeeded'
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8090/api/v1')
    parser.add_argument('--token-file', type=Path, default=Path('deploy/.state/test-token'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.token_file.read_text().strip(), args.output)
