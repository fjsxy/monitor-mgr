"""仅用于本 run 独占 Helm/PVC 的真实旧 schema 夹具；不连接用户既存平台。"""
import json


def upgrade_v10(s, missing_ids=(), draft_revisions=None):
    """停止独占平台，真实裁去 v11+ 结构，再启动同源 Pod 执行产品迁移。"""
    cookies = s.cookies
    old_uid = s.platform_pod_uid
    proof = s.proof('historical-before')
    s.forward.terminate()
    s.forward.wait(timeout=5)
    del s.forward
    s.k('scale', 'deployment/monitor-mgr', '-n', s.ns, '--replicas=0')
    s.k('wait', '--for=delete', 'pods', '-l', 'app=monitor-mgr', '-n', s.ns, '--timeout=60s')
    # 关键：只有本例私有平台停止后才挂原PVC；既有版本1–10的数据、UID、密文和审计保持原值。
    script = """import json,sqlite3
c=sqlite3.connect('/data/platform.db');c.execute('BEGIN')
for table in ['resource_progress','operation_resources','version_resources','instance_resource_identities','instance_management_confirmations']:
    c.execute('DROP TABLE IF EXISTS '+table)
for column in ['creator_user_id','creator_role']:
    c.execute('ALTER TABLE instances DROP COLUMN '+column)
for actor,action,target,key,raw in c.execute("SELECT actor_id,action,target_id,command_key,result_json FROM command_results WHERE action='CREATE_INSTANCE'").fetchall():
    value=json.loads(raw)
    if value['id'] in MISSING:
        c.execute('DELETE FROM command_results WHERE actor_id=? AND action=? AND target_id=? AND command_key=?',(actor,action,target,key));continue
    for field in ['creatorUserId','creatorRole','creatorIdentityKnown','canManage','readOnlyReason','managementConfirmation']:
        value.pop(field,None)
    c.execute('UPDATE command_results SET result_json=? WHERE actor_id=? AND action=? AND target_id=? AND command_key=?',(json.dumps(value),actor,action,target,key))
for instance,revision in REVISIONS.items():
    c.execute('UPDATE drafts SET revision=? WHERE instance_id=?',(revision,instance))
c.execute('DELETE FROM schema_migrations WHERE version>10');c.commit()
assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==10
assert not {'creator_user_id','creator_role'} & {r[1] for r in c.execute('PRAGMA table_info(instances)')}
print(json.dumps({'legacyVersion':10,'integrity':c.execute('PRAGMA integrity_check').fetchone()[0]}))
""".replace('MISSING', repr(list(missing_ids))).replace('REVISIONS', repr(draft_revisions or {}))
    assert s.job('historical-v10', script) == {'legacyVersion': 10, 'integrity': 'ok'}
    s.k('scale', 'deployment/monitor-mgr', '-n', s.ns, '--replicas=1')
    s.k('rollout', 'status', 'deployment/monitor-mgr', '-n', s.ns, '--timeout=120s')
    pods = [p for p in s.get('pods', '-n', s.ns, '-l', 'app=monitor-mgr', target='control')['items'] if not p['metadata'].get('deletionTimestamp')]
    assert len(pods) == 1
    s.pod = pods[0]['metadata']['name']
    s.platform_pod_uid = pods[0]['metadata']['uid']
    assert old_uid != s.platform_pod_uid
    s.cookies = cookies
    s.connect()
    assert s.proof('historical-after') == proof
