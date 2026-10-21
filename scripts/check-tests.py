#!/usr/bin/env python3
"""校验实际 UT 原生报告的完整性，只输出可公开的状态、耗时和 candidate。"""
import json
from pathlib import Path
import subprocess
from testing_contract import validate_specs

root=Path(__file__).resolve().parents[1]
suites=[]
for package in ('control','platform'):
    report=json.loads((root/'internal'/package/'.scratch/ut.json').read_text());assert len(report)==1;suites+=report
candidate=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
catalog=json.loads((root/'docs/acceptance/cases.json').read_text())
expected=[c['id'] for c in catalog['cases'] if c['type']=='ut']
combined=[dict(SuiteSucceeded=all(s.get('SuiteSucceeded') for s in suites),SuiteHasProgrammaticFocus=any(s.get('SuiteHasProgrammaticFocus') for s in suites),SuiteConfig={'DryRun':any(s.get('SuiteConfig',{}).get('DryRun') for s in suites)},SpecReports=[spec for s in suites for spec in s['SpecReports']])]
specs=validate_specs(combined,expected,candidate,candidate)
result=dict(candidate=candidate,passed=True,count=len(specs),cases=[dict(id=s['LeafNodeText'].split(' ')[0],state=s['State'],seconds=s['RunTime']/1e9) for s in specs])
(root/'.scratch/ut-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('Ginkgo UT 全部通过：18/18')
