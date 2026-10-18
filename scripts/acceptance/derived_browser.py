#!/usr/bin/env python3
"""从已通过的同 C 完整 FULL 原件派生补充 B 收据；不重新执行浏览器。"""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

import gate
import registry
from exporter import _safe
from session import atomic_json, stamp

PARENTS = {'REG18-PATTERN-011': 'U02.primary', 'REG19-HTTP-I64-001': 'U05.primary'}
PATTERN_ATTACHMENTS = ('browser-console.json', 'browser-layout.json',
                       'account-pattern-login.json', 'account-pattern-create.json')


def references(value):
    """只遍历显式字节引用；普通业务路径或浏览器附件字符串不能成为文件授权。"""
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield value
        else:
            for child in value.values():yield from references(child)
    elif isinstance(value, list):
        for child in value:yield from references(child)


def copy_closure(reader, wrapper_ref, destination, prefix, canaries=()):
    """按父布局原样封存必要原件闭包，JSON 内相对路径不改写，原 hash 不变。"""
    gate.require(re.fullmatch(r'parents/fixed/[0-9a-f]{64}', prefix or '') is not None, 'derived-prefix-invalid')
    destination = Path(destination).resolve()
    pending = [(wrapper_ref, '')]
    copied = {}
    original_bases = {}
    while pending:
        item, base = pending.pop()
        relative = item.get('path')
        gate.require(isinstance(relative, str), 'invalid-reference')
        normalized = dict(item, path=str(PurePosixPath(base) / relative) if base else relative)
        data = reader.read(normalized)
        key = normalized['path']
        if key in copied:
            gate.require(copied[key]['sha256'] == normalized['sha256'], 'conflicting-reference')
            continue
        _safe(key, data, normalized['kind'], normalized['visibility'], canaries)
        path = destination / prefix / key
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        gate.require(not any(p.is_symlink() for p in [path, *path.parents] if p != destination.parent), 'path-escape')
        if path.exists():gate.require(path.is_file() and path.read_bytes() == data, 'derived-existing-bytes-changed')
        else:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        copied[key] = dict(normalized, path=prefix + '/' + key)
        if normalized['kind'] not in ['receipt', 'safe-json', 'browser-report']:
            continue
        value = gate.json_bytes(data)
        content_base = original_bases.get(key, '')
        if isinstance(value, dict) and isinstance(value.get('originalReceipt'), dict):
            original = value['originalReceipt']['path']
            original_bases[original] = value.get('originalEvidenceBase', str(PurePosixPath(original).parent))
        pending.extend((child, content_base) for child in references(value))
    return list(copied.values())


def pattern_proof(attachments):
    """核对原 Chromium DOM 的20个值、v编译和原生 patternMismatch，不重算替代实际值。"""
    gate.require(set(PATTERN_ATTACHMENTS) <= set(attachments), 'pattern-attachments-missing')
    console, layout = attachments['browser-console.json'], attachments['browser-layout.json']
    gate.require(console.get('pageErrors') == [] and isinstance(console.get('console'), list), 'pattern-browser-error')
    gate.require(not re.search(r'pattern.*(?:invalid|SyntaxError)|Invalid regular expression',
                              json.dumps(console['console']), re.I), 'pattern-browser-error')
    gate.require(isinstance(layout.get('viewport'), (int, float)) and not isinstance(layout['viewport'], bool)
                 and isinstance(layout.get('scrollWidth'), (int, float))
                 and 0 < layout['scrollWidth'] <= layout['viewport'], 'pattern-layout-invalid')
    values = ['a', 'user.name', 'user-name', 'user_name', 'aA09._-', '中文', 'a b', 'a/b', '-name', 'a' * 64]
    forms = {}
    for name in ['login', 'create']:
        value = attachments['account-pattern-' + name + '.json']
        gate.require(isinstance(value.get('pattern'), str) and bool(value['pattern']) and
                     value.get('compilationError') == '', 'pattern-compilation-failed')
        inputs = value.get('inputs')
        gate.require(isinstance(inputs, list) and len(inputs) == len(values), 'pattern-dom-values-invalid')
        for i, actual in enumerate(inputs):
            gate.require(actual.get('value') == values[i] and actual.get('valid') is (i < 5)
                         and actual.get('patternMismatch') is (i >= 5), 'pattern-dom-values-invalid')
        forms[name] = copy.deepcopy(value)
    return {'inputCount': 20, 'forms': forms}


def browser_pass(report, selector):
    """只接受原实际 selector 的完整成功事件，重试中的失败/skip也不能抹去。"""
    specs = []
    def visit(value):
        specs.extend(s for s in value.get('specs', []) if s.get('title', '').split(' ', 1)[0] == selector)
        for child in value.get('suites', []):visit(child)
    visit(report)
    gate.require(len(specs) == 1 and len(specs[0].get('tests', [])) == 1 and not report.get('errors'), 'derived-browser-event-missing')
    for test in specs[0]['tests']:
        gate.require(test.get('status') == 'expected' and len(test.get('results', [])) == 1 and
                     all(r.get('status') == 'passed' and not r.get('error') and not r.get('errors')
                         for r in test['results']), 'derived-browser-event-failed')


def derive(session, entry, parent_index):
    """固定父键和实际子步骤；新原件用 REG contract，父原件只封存、不改写。"""
    candidate = session.index['candidateCommit']
    parent_index = Path(parent_index)
    before = parent_index.read_bytes()
    # 关键：先实跑完整同C FULL gate；validator-test 或部分父run不能洗成派生passing。
    summary = gate.validate(parent_index, candidate, 'LOCAL_FULL', supplemental=False)
    gate.require(parent_index.read_bytes() == before, 'derived-parent-index-changed')
    parent = gate.json_bytes(before)
    variant = entry.get('variantId')
    canonical = [e for e in registry.entries('LOCAL_FULL', True) if e['variantId'] == variant and e['layer'] == 'B']
    gate.require(variant in PARENTS and entry.get('layer') == 'B' and len(canonical) == 1
                 and registry.contract(entry) == registry.contract(canonical[0])
                 and entry.get('contractFingerprint') == canonical[0]['contractFingerprint'], 'derived-contract-invalid')
    gate.require(session.index.get('profile') == 'LOCAL_FULL' and session.index.get('supplemental') is True
                 and session.index.get('testedTree') == parent['testedTree'] and session.index.get('dirty') is False
                 and all(session.index.get(k) == candidate for k in ['testedCommit', 'runnerCommit']), 'derived-source-invalid')
    fixed = PARENTS[variant]
    selected = [r for r in parent['results'] if r['variantId'] == fixed and r['layer'] == 'B']
    gate.require(len(selected) == 1, 'derived-parent-key-missing')
    reader = gate.Evidence(parent_index.parent)
    wrapper_ref = selected[0]['receipt'];wrapper = reader.json(wrapper_ref)
    raw_original = reader.json(wrapper['originalReceipt'])
    wrapper_refs = list(references(wrapper))
    def artifact(name, refs=wrapper_refs):
        found = {r['path']: r for r in refs if PurePosixPath(r['path']).name == name}
        gate.require(len(found) == 1, 'derived-attachment-missing')
        return next(iter(found.values()))
    if variant == 'REG18-PATTERN-011':
        report_ref = wrapper['browserEventRef'];browser_pass(reader.json(report_ref), 'U02.primary')
        attachments = {name: reader.json(artifact(name)) for name in PATTERN_ATTACHMENTS}
        proof = pattern_proof(attachments)
    else:
        evidence = raw_original.get('evidence', {})
        step = evidence.get('i64Browser')
        gate.require(isinstance(step, dict) and step.get('selector') == variant and
                     step.get('exitCode') == 0 and step.get('trace') == 'off', 'derived-i64-substep-missing')
        relative = step.get('report')
        gate.require(isinstance(relative, str), 'derived-i64-substep-missing')
        # normalize后的wrapper ref是本run路径；原业务report字符串仍是case相对路径。
        found = {r['path']: r for r in wrapper_refs if r['path'] == relative or r['path'].endswith('/' + relative)}
        gate.require(len(found) == 1, 'derived-i64-substep-missing')
        report_ref = next(iter(found.values()));browser_pass(reader.json(report_ref), variant)
        proof = {'selector': variant, 'parentSelector': 'U05.primary',
                 'source': '原C实际浏览器大整数断言与原report；没有另造观测值'}
    prefix = 'parents/fixed/' + hashlib.sha256(before).hexdigest()
    refs = copy_closure(reader, wrapper_ref, session.out, prefix,
                        tuple(v.encode() for v in getattr(session, 'canaries', ()) if v))
    def rebound(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:return dict(value, path=prefix + '/' + value['path'])
            return {k: rebound(v) for k, v in value.items()}
        if isinstance(value, list):return [rebound(v) for v in value]
        return value
    original = {**session.header(entry), 'outcome': 'passed', 'finishedAt': stamp(),
                'derivedFrom': rebound(wrapper_ref), 'parentVariantId': fixed,
                'parentContractFingerprint': wrapper['contractFingerprint'],
                'parentValidation': {'profile': 'LOCAL_FULL', 'count': summary['count'],
                                     'indexSHA256': hashlib.sha256(before).hexdigest()},
                'browserEventRef': rebound(report_ref), 'evidenceRefs': refs,
                'runtimeOS': wrapper['runtimeOS'], 'runtimeArch': wrapper['runtimeArch'],
                'productImage': rebound(wrapper['productImage']), 'platform': rebound(wrapper['platform']),
                'evidence': proof, 'cleanup': {'success': True, 'scope': '同C父FULL已闭合；本次仅复制原件闭包'}}
    path = Path(session.out) / 'original' / ('B.' + variant + '.json')
    gate.require(not path.exists(), 'derived-result-exists')
    atomic_json(path, original)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    wrapper = session.store_original_file(entry, path, original_evidence_base='')
    gate.require(hashlib.sha256(path.read_bytes()).hexdigest() == digest and
                 wrapper.get('originalReceipt', {}).get('sha256') == digest, 'derived-original-changed')
    return wrapper
