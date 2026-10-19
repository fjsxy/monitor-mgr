"""平台三项K与账号七项B的窄适配；部署、锁、ledger和清理由Session负责。"""
import hashlib
import base64
import json
import os
import pathlib
import re
import uuid

import registry
import gate

ROOT = pathlib.Path(__file__).resolve().parents[2]


K_CASES = ['T01.primary', 'T02.primary', 'T03.primary']
B_CASES = ['I01.primary', 'I07.reset', 'I07.disable', 'I07.role-change',
           'I07.last-admin-disable', 'I07.last-admin-demote', 'I07.disabled-owner']


def _entry(entry):
    """先校验精确固定键与profile，未知输入不产生任何副作用。"""
    variant, profile = entry.get('variantId'), entry.get('profile')
    if variant not in K_CASES + B_CASES:
        raise ValueError('平台适配未注册此用例')
    registered = registry.entries(profile, focus=[variant])
    if len(registered) != 1:
        raise ValueError('平台适配需要唯一固定键')
    return registered[0]


def before_deploy(suite, entry):
    """I01必须真实空库；其余每例保持独立管理员基线。"""
    selected = _entry(entry)
    suite.a.initialize = selected['variantId'] != 'I01.primary'
    suite.a.prepare_target = False


def browser_plan(entry):
    """只选择已有spec与全部必需test，不能用grep意外跑少/混入其他例。"""
    selected = _entry(entry)
    variant = selected['variantId']
    if variant not in B_CASES:
        raise ValueError('此用例不是账号浏览器用例')
    selectors = selected['testSelectors']
    grep = '(?:^|\\s)(?:' + '|'.join(re.escape(s) for s in selectors) + ')(?: |$)'
    return dict(spec='tests/clusters.spec.ts' if variant == 'I07.disabled-owner' else 'tests/accounts.spec.ts',
                selectors=selectors, grep=grep)


def browser_harness(entry, private_directory):
    """外部生成wrapper，导入C中的原spec；追加观测，不复制或修改原断言。"""
    plan = browser_plan(entry)
    private = pathlib.Path(private_directory).absolute()
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    if private.is_symlink():
        raise ValueError('浏览器私有目录不能为symlink')
    private.chmod(0o700)
    package = (ROOT / 'web/node_modules/@playwright/test/index.mjs').as_uri()
    spec = ROOT / 'web' / plan['spec']
    wrapper = private / 'original.spec.mjs'
    body = '''import {test,expect} from PACKAGE;
const observations=new Map();
test.beforeEach(async({page},info)=>{
 const value={console:[],pageErrors:[]};observations.set(info.testId,value);
 page.on('console',message=>value.console.push({type:message.type(),text:message.text()}));
 page.on('pageerror',error=>value.pageErrors.push(String(error)));
});
test.afterEach(async({page},info)=>{
 const value=observations.get(info.testId)||{console:[],pageErrors:[]};
 const layout=await page.evaluate(()=>({viewportWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth,viewportHeight:innerHeight}));
 await info.attach('browser-console.json',{body:JSON.stringify(value),contentType:'application/json'});
 await info.attach('browser-layout.json',{body:JSON.stringify(layout),contentType:'application/json'});
 const screenshot=info.outputPath('account-final.png');await page.screenshot({path:screenshot});
 await info.attach('account-final.png',{path:screenshot,contentType:'image/png'});
 expect(value.pageErrors).toEqual([]);expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
});
await import(SPEC);
'''.replace('PACKAGE', json.dumps(package)).replace('SPEC', json.dumps(spec.as_uri()))
    wrapper.write_text(body)
    config = private / 'playwright.config.mjs'
    config.write_text('import {defineConfig} from ' + json.dumps(package) + ';\nconst configuration=' +
        json.dumps(dict(testDir=str(private), testMatch='**/original.spec.mjs', workers=1,
            use=dict(viewport=dict(width=1137, height=954), screenshot='only-on-failure', trace='off'),
            reporter=[['json', dict(outputFile=str(private / 'playwright.json'))]])) +
        ';\nconfiguration.use.baseURL=process.env.BASE_URL;export default defineConfig(configuration);\n')
    wrapper.chmod(0o600)
    config.chmod(0o600)
    command = ['npm', '--prefix', 'web', 'run', 'test:e2e', '--', '--config', str(config),
               '--grep', plan['grep'], '--output', str(private / 'artifacts')]
    return dict(**plan, command=command, report=private / 'playwright.json',
                harnessSHA256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                configSHA256=hashlib.sha256(config.read_bytes()).hexdigest(),
                originalSpecSHA256=hashlib.sha256(spec.read_bytes()).hexdigest())


def _check(condition, code):
    """用固定诊断码保留失败，不回显可能含认证信息的原异常。"""
    if not condition:
        raise AssertionError(code)


def _specs(report):
    """递归读取Playwright实际收集/执行的spec。"""
    values = list(report.get('specs', []))
    for suite in report.get('suites', []):
        values.extend(_specs(suite))
    return values


def _ref(s, path, kind='safe-json'):
    """公开文件保持原字节，生成相对组件输出根的hash引用。"""
    return dict(path=str(path.relative_to(s.out)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                visibility='public', kind=kind)


def _captured(s, command, private_path, env=None, timeout=90):
    """所有外部子进程交Session登记；stdout/stderr不得先到公开console。"""
    result = s.run_captured(command, cwd=ROOT, env=env or dict(os.environ),
                            private_path=private_path, timeout=timeout)
    _check(result.returncode == 0, 'PLATFORM_CASE_PROCESS_FAILED')
    return result


def _process_json(s, command, label):
    """只解析受保护捕获结果，不将子进程原输出送到console。"""
    private = s.out / '.private' / (label + '-' + uuid.uuid4().hex + '.log')
    private.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = _captured(s, command, private)
    raw = result.stdout if getattr(result, 'stdout', None) is not None else private.read_bytes()
    return json.loads(raw)


def _browser(s, entry):
    """先确认原测试完整收集，再执行并校验安全附件闭包。"""
    private = s.out / '.private' / ('platform-browser-' + uuid.uuid4().hex)
    plan = browser_harness(entry, private)
    env = dict(os.environ, BASE_URL=s.base, ADMIN_PASSWORD=s.password,
               CLUSTER_KUBECONFIG=str(s.a.control_pod_config))
    listed = private / 'collected.json'
    _captured(s, [*plan['command'], '--list', '--reporter=json'], private / 'collection.log',
              env=dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(listed)))
    collected = _specs(json.loads(listed.read_text()))
    actual = [spec['title'].split(' ', 1)[0] for spec in collected]
    _check(actual == plan['selectors'], 'BROWSER_ORIGINAL_TEST_COLLECTION_MISMATCH')
    _captured(s, plan['command'], private / 'execution.log',
              env=dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(plan['report'])), timeout=240)
    report_bytes = plan['report'].read_bytes()
    report = json.loads(report_bytes)
    specs = _specs(report)
    _check([spec['title'].split(' ', 1)[0] for spec in specs] == plan['selectors'] and not report.get('errors'),
           'BROWSER_RESULT_SET_MISMATCH')
    values = [s.password, 'temporary-password', 'personal-password', 'reset-password-123',
              *getattr(s, 'secret_values', {}).values()]
    canaries = [value.encode() for value in values if isinstance(value, str) and value]
    public = {'playwright.json': report_bytes, 'collection.json': listed.read_bytes()}
    for spec in specs:
        _check(len(spec.get('tests', [])) == 1, 'BROWSER_RESULT_COUNT_MISMATCH')
        test = spec['tests'][0]
        _check(test.get('status') == 'expected' and len(test.get('results', [])) == 1,
               'BROWSER_SKIPPED_OR_RETRIED')
        result = test['results'][0]
        _check(result.get('status') == 'passed' and not result.get('error') and not result.get('errors'),
               'BROWSER_ASSERTION_FAILED')
        attachments = {}
        for attachment in result.get('attachments', []):
            name = pathlib.Path(attachment.get('name', '')).name
            if name not in ['browser-console.json', 'browser-layout.json', 'account-final.png']:
                continue
            if attachment.get('body'):
                raw = base64.b64decode(attachment['body'], validate=True)
            else:
                source = pathlib.Path(attachment['path'])
                _check(not source.is_symlink() and source.resolve().is_relative_to(private.resolve()),
                       'BROWSER_ATTACHMENT_OUTSIDE_PRIVATE_ROOT')
                raw = source.read_bytes()
            attachments[name] = raw
        _check(set(attachments) == {'browser-console.json', 'browser-layout.json', 'account-final.png'},
               'BROWSER_REQUIRED_ATTACHMENT_MISSING')
        console = json.loads(attachments['browser-console.json'])
        layout = json.loads(attachments['browser-layout.json'])
        _check(not console['pageErrors'] and layout['scrollWidth'] <= layout['viewportWidth'],
               'BROWSER_LAYOUT_OR_PAGE_ERROR')
        for name, raw in attachments.items():
            public[spec['title'].split(' ', 1)[0] + '/' + name] = raw
    # 关键：先扫描原report及解码附件，再原字节公开；敏感内容不做原地改写补绿。
    _check(not any(value in raw for raw in public.values() for value in canaries), 'BROWSER_SENSITIVE_OUTPUT')
    output = s.out / 'browser' / entry['variantId']
    output.mkdir(parents=True, exist_ok=False)
    refs = []
    for name, raw in public.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        refs.append(_ref(s, path, 'screenshot' if name.endswith('.png') else 'browser-report' if name == 'playwright.json' else 'safe-json'))
    return dict(testSelectors=plan['selectors'], report=str((output / 'playwright.json').relative_to(s.out)),
                browserEventRef=_ref(s, output / 'playwright.json', 'browser-report'),
                originalSpecSHA256=plan['originalSpecSHA256'], harnessSHA256=plan['harnessSHA256'],
                configSHA256=plan['configSHA256'], collectedSelectors=actual, commandExitCode=0,
                platformPodUID=s.initial_uid, trace='off', evidenceRefs=refs)


def _descriptor(s, entry):
    """准备器描述必须来自同一候选、profile和版本化source-lock。"""
    value = s.a.environment_descriptor
    value = json.loads(pathlib.Path(value).read_text()) if not isinstance(value, dict) else value
    lock = json.loads((ROOT / 'scripts/acceptance/source-lock.json').read_text())
    targets = {'control', 'data'} if entry['profile'] == 'LOCAL_FULL' else {'control'}
    _check(value.get('profile') == entry['profile'] and value.get('source') == s.a.source and
           value.get('sourceLock') == lock and set(value.get('targets', {})) == targets,
           'PLATFORM_ENVIRONMENT_DESCRIPTOR_MISMATCH')
    _check(value.get('monitorOpCommit') == lock['monitorOp']['source'], 'PLATFORM_OPERATOR_SOURCE_MISMATCH')
    return value, lock


def _copy_metadata(s, descriptor, proof):
    """只读prepare明确批准的原JSON元数据；原凭据、层和tar不进入此入口。"""
    declared_root = pathlib.Path(descriptor['privateRoot']).absolute()
    root = declared_root.resolve()
    reader = gate.Evidence(root)
    refs, contents = [], {}
    _check(bool(proof.get('evidenceRefs')), 'PLATFORM_IMAGE_ORIGINAL_METADATA_MISSING')
    for original in proof['evidenceRefs']:
        source = pathlib.Path(original['path'])
        _check(source.is_absolute() and (source.is_relative_to(declared_root) or source.is_relative_to(root)) and source.suffix == '.json',
               'PLATFORM_METADATA_PATH_INVALID')
        relative = str(source.relative_to(declared_root if source.is_relative_to(declared_root) else root))
        data = reader.read(dict(path=relative, sha256=original['sha256'], kind='safe-json', visibility='private'))
        value = gate.json_bytes(data)
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        contents[digest] = value
        kind = 'image-config' if isinstance(value, dict) and 'architecture' in value else (
            'image-manifest' if isinstance(value, dict) and ('manifests' in value or 'config' in value) else 'safe-json')
        output = s.out / 'platform-provenance' / (digest.split(':')[1] + '.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            _check(output.read_bytes() == data, 'PLATFORM_METADATA_COLLISION')
        else:
            output.write_bytes(data)
        refs.append(_ref(s, output, kind))
    return refs, contents


def _topology(s, entry):
    """T01重新读取真实环境身份与状态，准备器旧绿色标志不能代替回读。"""
    descriptor, lock = _descriptor(s, entry)
    arch = descriptor['platform'].split('/')[1]
    _check(arch == ('arm64' if entry['profile'] == 'LOCAL_FULL' else 'amd64'), 'PLATFORM_ARCH_MISMATCH')
    refs, product_proofs = [], {}
    for image, revision in [(descriptor['image'], s.a.source),
                            (descriptor['fixtureImages']['environment'], s.a.source),
                            (descriptor['monitorOpImage'], lock['monitorOp']['source'])]:
        proof = descriptor['imageProofs'][image]
        _check(proof.get('source') == revision and proof.get('platform') == descriptor['platform'] and
               proof.get('originalContentBytesVerified') is True, 'PLATFORM_IMAGE_PROVENANCE_MISSING')
        if image == descriptor['monitorOpImage']:
            from image_provenance import source_config_bytes
            # 私有Operator原config只私有回读；公开仅保留已核digest/revision和实际UID投影。
            copied, contents = [], {proof['configDigest']: json.loads(source_config_bytes(descriptor, image))}
        else:
            copied, contents = _copy_metadata(s, descriptor, proof)
        config = contents.get(proof['configDigest'], {})
        _check(config.get('architecture') == arch and config.get('os') == 'linux' and
               config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') == revision,
               'PLATFORM_IMAGE_CONFIG_SOURCE_MISMATCH')
        # private链已在原私有路径核完；此处只检查确已公开复制的manifest，不能要求私有原件进入contents。
        if proof.get('manifestDigest') and image != descriptor['monitorOpImage']:
            _check(contents.get(proof['manifestDigest'], {}).get('config', {}).get('digest') == proof['configDigest'],
                   'PLATFORM_IMAGE_CONFIG_BINDING_MISMATCH')
        refs.extend(copied)
        product_proofs[image] = {k: proof.get(k) for k in ['configDigest', 'manifestDigest', 'source', 'platform']}
    for name in ['ksm', 'prometheus', 'promop', 'reloader', 'provisioner']:
        proof = descriptor['officialContent'][name]
        _check(proof.get('indexDigest') == lock['images'][name]['indexDigest'] and
               proof.get('platform') == descriptor['platform'] and proof.get('originalContentBytesVerified') is True,
               'PLATFORM_OFFICIAL_SOURCE_MISMATCH')
        copied, contents = _copy_metadata(s, descriptor, proof)
        index = contents.get(proof['indexDigest'], {})
        manifests = [m for m in index.get('manifests', []) if m.get('platform', {}).get('os') == 'linux' and
                     m.get('platform', {}).get('architecture') == arch]
        _check(len(manifests) == 1 and manifests[0]['digest'] == proof['manifestDigest'] and
               contents.get(proof['manifestDigest'], {}).get('config', {}).get('digest') == proof['configDigest'],
               'PLATFORM_OFFICIAL_CONTENT_CHAIN_MISMATCH')
        config = contents.get(proof['configDigest'], {})
        _check(config.get('os') == 'linux' and config.get('architecture') == arch, 'PLATFORM_OFFICIAL_ARCH_MISMATCH')
        refs.extend(copied)
    observed = {}
    for role, target in descriptor['targets'].items():
        uid = s.get('namespace', 'kube-system', target=role)['metadata']['uid']
        _check(uid == target['clusterUID'], 'PLATFORM_CLUSTER_UID_CHANGED')
        nodes = s.get('nodes', target=role)['items']
        expected_roles = ['control-plane', 'worker', 'worker'] if role == 'control' else ['control-plane']
        _check(sorted('control-plane' if 'node-role.kubernetes.io/control-plane' in n['metadata'].get('labels', {})
                      else 'worker' for n in nodes) == expected_roles, 'PLATFORM_NODE_ROLES_MISMATCH')
        expected_nodes = {n['name']: n['uid'] for n in target['nodeTopology']}
        _check({n['metadata']['name']: n['metadata']['uid'] for n in nodes} == expected_nodes,
               'PLATFORM_NODE_UID_CHANGED')
        for node in nodes:
            info = node['status']['nodeInfo']
            _check(info.get('architecture') == arch and info.get('operatingSystem') == 'linux' and
                   info.get('kubeletVersion') == 'v1.28.15' and any(c.get('type') == 'Ready' and
                   c.get('status') == 'True' for c in node['status'].get('conditions', [])), 'PLATFORM_NODE_NOT_READY')
            if role == 'data':
                _check(not node.get('spec', {}).get('unschedulable') and not node.get('spec', {}).get('taints'),
                       'PLATFORM_DATA_NODE_NOT_SCHEDULABLE')
        node_images = []
        for container in target['containers']:
            observed_container = _process_json(s, ['docker', 'inspect', '--format',
                '{"id":{{json .Id}},"image":{{json .Config.Image}},"runtimeImageID":{{json .Image}}}',
                container['name']], 'kind-node-image')
            _check(observed_container['id'] == container['id'] and
                   observed_container['image'] == lock['images']['kind_node']['image'] and
                   bool(re.fullmatch(r'sha256:[0-9a-f]{64}', observed_container['runtimeImageID'])),
                   'PLATFORM_KIND_NODE_IMAGE_MISMATCH')
            digests = _process_json(s, ['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}',
                observed_container['image']], 'kind-node-repository-digests')
            _check(any(d.endswith('@' + lock['images']['kind_node']['indexDigest']) for d in digests),
                   'PLATFORM_KIND_NODE_INDEX_MISMATCH')
            node_images.append(dict(node=container['name'], **observed_container,
                lockedIndexDigest=lock['images']['kind_node']['indexDigest']))
        _check({n['node'] for n in node_images} == set(expected_nodes), 'PLATFORM_KIND_NODE_SET_MISMATCH')
        crds = target['crds']
        required = {'kubestatemetrics.monitoring.fjsxy.github.io', 'prometheusagents.monitoring.coreos.com',
                    'servicemonitors.monitoring.coreos.com', 'podmonitors.monitoring.coreos.com',
                    'probes.monitoring.coreos.com', 'scrapeconfigs.monitoring.coreos.com'}
        _check(required <= {c['name'] for c in crds}, 'PLATFORM_REQUIRED_CRD_MISSING')
        for expected in crds:
            crd = s.get('crd', expected['name'], target=role)
            spec_hash = hashlib.sha256(json.dumps(crd['spec'], ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':')).encode()).hexdigest()
            _check(crd['metadata']['uid'] == expected['uid'] and spec_hash == expected['specSHA256'] and
                   any(c.get('type') == 'Established' and c.get('status') == 'True'
                       for c in crd.get('status', {}).get('conditions', [])), 'PLATFORM_CRD_CHANGED_OR_NOT_ESTABLISHED')
        controllers = []
        pods = s.get('pods', '-n', 'mm-phase1-operators', target=role)['items']
        for expected in target['controllers']:
            pod = next((p for p in pods if p['metadata']['uid'] == expected['podUID']), None)
            _check(pod is not None and not pod['metadata'].get('deletionTimestamp'), 'PLATFORM_CONTROLLER_UID_CHANGED')
            containers = pod.get('status', {}).get('containerStatuses', [])
            _check(containers and all(c.get('ready') for c in containers), 'PLATFORM_CONTROLLER_NOT_READY')
            status = containers[0]
            from image_provenance import runtime_image_proof
            def read_runtime(command):
                private = s.out / '.private' / ('runtime-image-' + uuid.uuid4().hex + '.log')
                return _captured(s, command, private).stdout
            runtime_proof = runtime_image_proof(descriptor, role, pod, pod['spec']['containers'][0], status,
                next(n for n in nodes if n['metadata']['name'] == pod['spec']['nodeName']),
                read=read_runtime, lock=lock, output=s.out)
            _check(pod['spec']['containers'][0]['image'] == expected['image'] and status['imageID'] == expected['imageID'],
                   'PLATFORM_CONTROLLER_IMAGE_CHANGED')
            deployment = s.get('deployment', expected['name'], '-n', 'mm-phase1-operators', target=role)
            baseline = [b for b in target['baseline'] if b['kind'] == 'Deployment' and
                        b['name'] == expected['name'] and b.get('namespace') == 'mm-phase1-operators']
            _check(len(baseline) == 1 and baseline[0]['uid'] == deployment['metadata']['uid'],
                   'PLATFORM_CONTROLLER_DEPLOYMENT_UID_CHANGED')
            owner = next((o for o in pod['metadata'].get('ownerReferences', []) if o.get('controller') and
                          o.get('kind') == 'ReplicaSet'), None)
            _check(owner is not None, 'PLATFORM_CONTROLLER_POD_OWNER_MISSING')
            replica_set = s.get('replicaset', owner['name'], '-n', 'mm-phase1-operators', target=role)
            _check(replica_set['metadata']['uid'] == owner['uid'] and any(o.get('controller') and
                   o.get('uid') == deployment['metadata']['uid'] for o in replica_set['metadata'].get('ownerReferences', [])),
                   'PLATFORM_CONTROLLER_OWNER_CHAIN_MISMATCH')
            controller = dict(expected, deploymentUID=deployment['metadata']['uid'], replicaSetUID=owner['uid'], runtimeProof=runtime_proof)
            if expected['name'] == 'prometheus-operator':
                raw = s.k('get', '--raw', '/api/v1/namespaces/mm-phase1-operators/pods/' +
                          pod['metadata']['name'] + ':8080/proxy/metrics', target=role).stdout
                _check(b'version="0.83.0"' in raw and b'revision="5cf2f5d' in raw and
                       b'prometheus_operator_ready{controller="prometheus-agent"} 1' in raw,
                       'PLATFORM_AGENT_CONTROLLER_BUILD_OR_READY_MISMATCH')
                controller['metrics'] = dict(version='0.83.0', revision='5cf2f5d', agentControllerReady=True)
            controllers.append(controller)
        _check({c['name'] for c in controllers} == {'prometheus-operator', 'monitor-mgr-monitor-op'},
               'PLATFORM_CONTROLLER_SET_MISMATCH')
        storage = target['storageClass']
        actual_storage = s.get('storageclass', storage['name'], target=role)
        _check(actual_storage['metadata']['uid'] == storage['uid'] and actual_storage['provisioner'] == storage['provisioner'] and
               actual_storage['metadata'].get('annotations', {}).get('storageclass.kubernetes.io/is-default-class') == 'true',
               'PLATFORM_DEFAULT_STORAGE_CHANGED')
        provisioner = s.get('deployment', 'local-path-provisioner', '-n', 'local-path-storage', target=role)
        _check(provisioner['spec']['template']['spec']['containers'][0]['image'] == lock['images']['provisioner']['image'],
               'PLATFORM_PROVISIONER_IMAGE_MISMATCH')
        provisioner_pods = s.get('pods', '-n', 'local-path-storage', target=role)['items']
        provisioner_pods = [p for p in provisioner_pods if not p['metadata'].get('deletionTimestamp') and
                            p['metadata']['name'].startswith('local-path-provisioner-')]
        _check(len(provisioner_pods) == 1 and all(c.get('ready') for c in
               provisioner_pods[0].get('status', {}).get('containerStatuses', [])), 'PLATFORM_PROVISIONER_NOT_READY')
        provisioner_status = provisioner_pods[0]['status']['containerStatuses'][0]
        proof = descriptor['officialContent']['provisioner']
        _check(any(provisioner_status['imageID'].endswith(proof[k]) for k in
                   ['indexDigest', 'manifestDigest', 'configDigest']), 'PLATFORM_PROVISIONER_RUNTIME_IMAGE_MISMATCH')
        volume = target['volumeProof']
        volume_evidence = target['volumeProofEvidence']
        _check(volume.get('uid') == 10001 and volume.get('journalMode') == 'wal' and volume.get('synchronous') == 2 and
               volume.get('lockBlocked') is True and volume.get('reopenValue') == 21 and volume.get('keyMode') == '0600' and
               volume_evidence.get('source') == s.a.source and volume_evidence.get('cleanup', {}).get('success') is True and
               volume_evidence.get('jobUID') and len(volume_evidence.get('pods', [])) == 1 and
               volume_evidence.get('image') == descriptor['fixtureImages']['environment'], 'PLATFORM_VOLUME_CAPABILITY_MISSING')
        copied, _ = _copy_metadata(s, descriptor, volume_evidence)
        refs.extend(copied)
        observed[role] = dict(clusterUID=uid, nodes=target['nodeTopology'], nodeImages=node_images,
                              crds=crds, controllers=controllers, storageClass=storage,
                              volume=dict(result=volume, jobUID=volume_evidence['jobUID'], pods=volume_evidence['pods'],
                                          cleanup=volume_evidence['cleanup']),
                              provisioner=dict(deploymentUID=provisioner['metadata']['uid'],
                                  podUID=provisioner_pods[0]['metadata']['uid'], imageID=provisioner_status['imageID']))
    _check(len({t['clusterUID'] for t in observed.values()}) == len(observed), 'PLATFORM_DUPLICATE_CLUSTER_UID')
    return dict(targets=observed, productImageProofs=product_proofs, evidenceRefs=list({r['path']: r for r in refs}.values()))


def _platform(s):
    """HTTP经实际Service，目标endpoint与本例原PodUID相同。"""
    forward = getattr(s, 'forward', None)
    _check(forward is not None and 'service/monitor-mgr' in getattr(forward, 'args', []) and
           forward.poll() is None, 'PLATFORM_SERVICE_TRANSPORT_REQUIRED')
    service = s.get('service', 'monitor-mgr', '-n', s.ns, target='control')
    _check(len(service['spec']['ports']) == 1 and service['spec']['ports'][0]['port'] == 8080 and
           service['spec']['ports'][0]['targetPort'] in ['http', 8080], 'PLATFORM_RPC_SERVICE_EXPOSED')
    pods = s.get('pods', '-n', s.ns, '-l', 'app=monitor-mgr', target='control')['items']
    pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    _check(len(pods) == 1 and pods[0]['metadata']['uid'] == s.initial_uid, 'PLATFORM_POD_IDENTITY_CHANGED')
    pod = pods[0]
    _check(len(pod['spec']['containers']) == 1 and not pod['spec'].get('hostNetwork') and
           any(c.get('type') == 'Ready' and c.get('status') == 'True'
               for c in pod.get('status', {}).get('conditions', [])), 'PLATFORM_SINGLE_PROCESS_POD_NOT_READY')
    slices = s.get('endpointslices', '-n', s.ns, '-l', 'kubernetes.io/service-name=monitor-mgr', target='control')['items']
    endpoints = [e for value in slices for e in value.get('endpoints', []) if e.get('conditions', {}).get('ready') is True]
    _check(endpoints and {e.get('targetRef', {}).get('uid') for e in endpoints} == {s.initial_uid},
           'PLATFORM_SERVICE_ENDPOINT_UID_MISMATCH')
    return pod, dict(serviceUID=service['metadata']['uid'], serviceName='monitor-mgr',
                     podUID=s.initial_uid, nodeName=pod['spec']['nodeName'],
                     imageID=pod['status']['containerStatuses'][0]['imageID'])


def _single_process(s):
    """T02用同一HTTP requestId串起Service入口和真实Kitex服务端。"""
    pod, proof = _platform(s)
    s.call('GET', '/session')
    trace = s.traces[-1]
    request_id = trace.get('requestId')
    _check(trace.get('method') == 'GET' and trace.get('path') == '/session' and trace.get('status') == 200 and request_id,
           'PLATFORM_HTTP_REQUEST_ID_MISSING')
    raw = s.k('-n', s.ns, 'logs', 'pod/' + s.pod, '--since=10m', target='control').stdout
    events = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            events.append(value)
    matches = [e for e in events if e.get('requestId') == request_id and e.get('transport') == 'kitex-server']
    ready = [e for e in events if e.get('msg') == '平台已就绪' and e.get('source') == s.a.source and
             e.get('rpc') == '127.0.0.1:8888']
    _check(matches and ready, 'PLATFORM_REAL_KITEX_SERVER_EVIDENCE_MISSING')
    volume = next((v for v in pod['spec']['volumes'] if v.get('persistentVolumeClaim')), None)
    _check(volume is not None and any(m.get('mountPath') == '/data' and m.get('name') == volume['name'] and
           not m.get('readOnly') for m in pod['spec']['containers'][0].get('volumeMounts', [])),
           'PLATFORM_DATA_VOLUME_NOT_MOUNTED')
    pvc = s.get('pvc', volume['persistentVolumeClaim']['claimName'], '-n', s.ns, target='control')
    _check(pvc.get('status', {}).get('phase') == 'Bound', 'PLATFORM_DATA_VOLUME_NOT_BOUND')
    return dict(**proof, requestId=request_id, matchedKitexRequestCount=len(matches), source=s.a.source,
                rpc='127.0.0.1:8888', containerCount=1, dataPVCUID=pvc['metadata']['uid'],
                httpServiceOnly=True)


def _dual_target(s, entry):
    """T03复用原两类组件业务断言；外层只补同候选目标身份绑定。"""
    from agent_cases import run_case as original_case
    descriptor, _ = _descriptor(s, entry)
    _check(set(descriptor['targets']) == {'control', 'data'}, 'PLATFORM_DUAL_TARGET_REQUIRED')
    s.prepare_target(0, 'control')
    evidence = original_case(s, 'T03.primary')
    _check(evidence['clusterUIDs'] == [descriptor['targets'][role]['clusterUID'] for role in ['control', 'data']] and
           len({evidence[role][kind]['cr']['metadata']['uid'] for role in ['control', 'data']
                for kind in ['ksm', 'agent']}) == 4,
           'PLATFORM_DUAL_TARGET_IDENTITIES_NOT_DISTINCT')
    return evidence


def run_case(s, entry):
    """仅执行本例断言；Session保证此前deploy、此后finally和原UID恢复。"""
    entry = _entry(entry)
    variant = entry['variantId']
    if variant in B_CASES:
        _platform(s)
        return _browser(s, entry)
    if variant == 'T01.primary':
        evidence = _topology(s, entry)
        # 保留原T01真实Agent发布、PVC和运行时断言；SMOKE只使用声明的control。
        from agent_cases import published
        s.prepare_target(0, 'control')
        instance, operation = published(s)
        evidence.update(operation=operation, agent=s.runtime(instance))
    else:
        evidence = _single_process(s) if variant == 'T02.primary' else _dual_target(s, entry)
    refs = evidence.pop('evidenceRefs', [])
    output = s.out / (variant + '.platform-evidence.json')
    output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return dict(**evidence, evidenceRefs=[_ref(s, output), *refs])
