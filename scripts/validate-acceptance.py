#!/usr/bin/env python3
"""校验验收计划的身份、来源和门禁覆盖，不执行产品测试。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


def validate(document):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def filled(value):
        return isinstance(value, str) and bool(value.strip())

    def text_list(value):
        return isinstance(value, list) and bool(value) and all(filled(x) for x in value)

    check(document.get('schema_version') == 1, '不支持的 schema_version')
    check(document.get('status') == 'planned_not_executed', '计划不能声明产品执行结果')
    requirements = document.get('requirements', {})
    profiles = document.get('profiles', {})
    cases = document.get('cases', [])
    check(set(requirements) == {'TOPO', 'AUTH', 'PERSIST', 'KSM', 'AGENT', 'PUBLISH', 'OBS', 'UI', 'API'},
          '需求来源组不完整或出现未知来源')
    for key, value in requirements.items():
        check(isinstance(value, list) and len(value) == 2 and all(filled(x) for x in value)
              and value[1].startswith('https://github.com/fjsxy/monitor-mgr/issues/'),
              f'{key}: 需求来源不是有效决议链接')
    check(set(profiles) == {'LOCAL_FULL', 'CI_FAST', 'CI_SMOKE'}, '执行 profile 不完整')
    expected_ids = {f'{prefix}{i:02}' for prefix, count in
                    [('T', 8), ('I', 7), ('C', 9), ('K', 10), ('A', 13),
                     ('P', 14), ('R', 8), ('U', 6), ('F', 5)] for i in range(1, count + 1)}
    ids, variant_ids, covered = [], [], set()
    counts = Counter()
    variant_counts = Counter()
    for case in cases:
        case_id = case.get('id', '<无 ID>')
        ids.append(case_id)
        for key in ['purpose', 'steps', 'expected']:
            value = case.get(key)
            check(filled(value), f'{case_id}: 缺少 {key}')
            if filled(value):
                check(not re.search(r'\b(?:TODO|TBD)\b|待补|占位', value), f'{case_id}: {key} 存在占位内容')
        for key in ['preconditions', 'cleanup', 'evidence']:
            check(text_list(case.get(key)), f'{case_id}: 缺少有效 {key}')
        req = case.get('requirements', [])
        assigned = case.get('profiles', [])
        check(text_list(req) and set(req) <= set(requirements), f'{case_id}: 未知或缺失需求来源')
        covered.update(req)
        check(text_list(assigned) and len(assigned) == len(set(assigned)) and set(assigned) <= set(profiles),
              f'{case_id}: 未知、重复或缺失 profile')
        check('LOCAL_FULL' in assigned, f'{case_id}: 缺少完整矩阵覆盖')
        check(case.get('layer') in {'Q', 'K', 'B'}, f'{case_id}: 未知测试层级')
        check(('CI_FAST' in assigned) == (case.get('layer') == 'Q'), f'{case_id}: 快测层与 profile 不一致')
        check(case.get('target') in {'none', 'control', 'multi'}, f'{case_id}: 未知目标拓扑')
        check(not ('CI_SMOKE' in assigned and case.get('target') == 'multi'), f'{case_id}: smoke 依赖双集群')
        check(case.get('status') == 'planned_not_executed', f'{case_id}: 计划不能冒充执行结果')
        variants = case.get('variants', [])
        check(isinstance(variants, list) and bool(variants), f'{case_id}: 子例未展开')
        for variant in variants:
            variant_id = variant.get('id', '')
            variant_ids.append(variant_id)
            check(bool(re.fullmatch(re.escape(case_id) + r'\.[a-z0-9][a-z0-9-]*', variant_id)),
                  f'{case_id}: 子例 ID 非稳定父级命名')
            for key in ['steps', 'expected']:
                check(filled(variant.get(key)), f'{variant_id}: 缺少 {key}')
            check(variant.get('status') == 'planned_not_executed', f'{variant_id}: 计划不能声明运行成功')
        counts.update(assigned)
        for profile in assigned:
            variant_counts[profile] += len(variants)
    check(len(ids) == len(set(ids)), '基础用例 ID 重复')
    check(set(ids) == expected_ids and len(ids) == 80, '80 个已批准基础用例被遗漏或替换')
    check(len(variant_ids) == len(set(variant_ids)), '子例 ID 重复')
    check(covered == set(requirements), '需求来源组没有全覆盖')
    for profile, expected in [('LOCAL_FULL', 80), ('CI_FAST', 17), ('CI_SMOKE', 14)]:
        check(counts[profile] == expected, f'{profile}: 基础用例数量应为 {expected}')
        check(profiles.get(profile, {}).get('base_cases') == expected, f'{profile}: profile 声明数量不一致')
    return errors, dict(base_cases=len(ids), variants=len(variant_ids),
                        profiles=dict(counts), variant_profiles=dict(variant_counts),
                        product_tests='not_executed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'docs/acceptance/phase1.json')
    args = parser.parse_args()
    try:
        document = json.loads(args.manifest.read_text())
        errors, result = validate(document)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print('验收目录读取或结构失败：' + str(error), file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(json.dumps(dict(metadata_validation='passed', **result), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
