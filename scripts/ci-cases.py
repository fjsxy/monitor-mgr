#!/usr/bin/env python3
"""从固定注册表和当前测试源码生成 CI 清单；--check 拒绝遗失或过时的清单。"""
import argparse
import ast
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/acceptance'))
import registry

DESTINATION = ROOT / 'docs/acceptance/ci-cases.md'


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] + [
        '| ' + ' | '.join(cell(c) for c in row) + ' |' for row in rows]


def go_cases():
    """普通 CI 发现全部 It；显式环境才能启用的 Host 诊断另列为条件场景。"""
    cases = []
    paths = [path for folder in ['cmd', 'internal', 'scripts', 'tests']
             for path in (ROOT / folder).glob('**/*_test.go')]
    for path in paths:
        text = path.read_text()
        traditional = re.findall(r'^func (Test\w+)\(', text, re.M)
        suite = path.name.endswith('_suite_test.go') and len(traditional) == 1 and 'ginkgo.RunSpecs(' in text
        if traditional and not suite or 't.Run(' in text or '*testing.T' in text and not suite:
            raise ValueError('Go 场景必须使用 Ginkgo，testing.T 仅允许 suite 入口：' + str(path.relative_to(ROOT)))
    for path in sorted(paths):
        text = path.read_text()
        for match in re.finditer(r'var _ = ginkgo.It\("([^"]+)"', text):
            tail = text[match.end():]
            purpose = re.search(r'// 接下来([^\n]+)', tail)
            cases.append((match[1], str(path.relative_to(ROOT)), purpose[1] if purpose else '环境卷能力与 SQLite 持久化',
                          '显式 WORKBENCH_HOST_FIXTURE/STOP 才运行；普通 CI 跳过' if match[1] == 'TestWorkbenchBrowserFixture' else '全量 Go UT/race，不按业务 profile 删减'))
    return cases


def host_cases():
    """列举 unittest discover 会收集的模块顶层类方法，含同模块基类继承的用例。"""
    cases = []
    for path in sorted((ROOT / 'scripts/acceptance').glob('*test.py')):
        module = ast.parse(path.read_text())
        classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
        def methods(node):
            values = {}
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in classes:
                    values.update(methods(classes[base.id]))
            values.update({n.name: ast.get_docstring(n) or ast.get_docstring(module) or '验证验收工具的输入、结果与错误边界' for n in node.body
                           if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')})
            return values
        for name, node in classes.items():
            for method, purpose in sorted(methods(node).items()):
                cases.append((path.stem + '.' + name + '.' + method, purpose))
    return cases


def rationale(entry, profile):
    """选择依据按所需环境与故障风险表达，不能把 Host Q 等同于真实集群验收。"""
    if profile == 'CI_FAST':
        return '无需真实集群即可重复验证的' + {
            'T': '启动和就绪边界', 'I': '会话与授权边界', 'C': '集群身份和凭据拒绝边界',
            'K': '配置身份与输入边界', 'P': '发布事务和竞争边界', 'R': '状态投影和请求预算边界',
            'F': '错误处理及恢复边界'}.get(entry['caseId'][0], '平台契约')
    return '单 control 集群覆盖' + {
        'T': '部署来源与实际 Pod 就绪', 'I': '账号和真实入口', 'C': '注册与凭据链路',
        'K': 'KSM 真实工作负载', 'A': 'Agent 真实工作负载与存储',
        'P': '发布或删除完成', 'R': '实际运行观测', 'U': '关键浏览器操作'}.get(entry['caseId'][0], '核心业务闭环')


def render():
    go, host = go_cases(), host_cases()
    workflow = (ROOT / '.github/workflows/phase1.yml').read_text()
    makefile = (ROOT / 'Makefile').read_text()
    # 此指纹让 workflow 或 Makefile 的入口调整也触发清单复核。
    digest = hashlib.sha256((workflow + '\n' + makefile).encode()).hexdigest()
    out = ['# 一期 CI 用例与选取标准', '',
           '由 `make ci-cases` 从源码、固定注册表、workflow 与 Makefile 生成；`make ci-cases-check` 校验一致性。修改用例或入口后必须重新生成。', '',
           '<!-- CI 入口指纹：' + digest + ' -->', '',
           '## 执行范围与准入标准', '',
           '- `fast`：每次 main PR、main push 和手动运行执行 `make check`、`make check-generated`，随后执行 CI_FAST 的固定 58 个 Q 键。',
           '- `smoke`：沿用 workflow 的可信来源限制；当前仅同仓 `codex/phase1-implementation` PR、main push，以及 main 或该实施分支的手动运行可以读取专用只读 deploy key。其他 PR 只跑 fast。',
           '- Go UT 全量纳入，不按 profile 抽样；race 同次执行。手写产品包 `internal/control` 与 `internal/platform` 的合计语句覆盖率至少 70%；排除生成的 `kitex_gen`、命令参数入口与验收工具。',
           '- Python 验收工具自测全部纳入：它们保护源码身份、用例选择、原始证据、敏感数据、独占 lane、停止恢复及清理，不能用产品用例替代。',
           '- CI_FAST 保留冻结目录中的快速契约边界：真实 HTTP→Kitex→SQLite 加受控 TLS 目标，适合每次变更重复执行；不声明真实 Kubernetes 或浏览器验收。',
           '- CI_SMOKE 保留固定 17 个键：在 Linux amd64 的单 control 集群以真实平台 Pod 验证核心链路，包含 KSM、Agent、PVC 与浏览器；不依赖 data 集群。',
           '- LOCAL_FULL 固定 207 个键覆盖双集群、破坏性故障与长链路恢复，需要独占环境，未全部放进常规 CI。supplemental 不计入固定键数；其 Go/工具回归仍随全量 UT 执行。',
           '- 键集沿用 `phase1.json` 与 `registry.py` 的冻结选择；新增关键风险先补用例，再按可重复性、所需拓扑、耗时和独占资源评估 profile，不能因某例易失败而删减。',
           '- passing 必须同时满足同一 candidate SHA、逐 selector 实际执行且通过、必选输入齐全、原证据可核验、敏感数据不公开、清理成功；失败、跳过、环境失败、未执行、缺失或 dry-run 均不能替代通过。', '',
           '## 各入口具体执行内容', '',
           '| CI 入口 | 执行内容 | 通过标准 |', '| --- | --- | --- |',
           '| `make acceptance-plan` | 固定验收目录、需求和稳定子例追踪 | 元数据与固定矩阵一致；不计产品通过 |',
           '| `make ci-cases-check` | 本文与当前源码、profile、workflow、Makefile 对照 | 完全一致 |',
           '| `make acceptance-host-check` | 下表全部 Python 工具自测，含真实 Ginkgo child 的调度正负控 | 所有 unittest 通过；合成业务仅记 Host 自测 |',
           '| `make test-race` | 下表全部 Go Ginkgo It/By、race 和覆盖率 | 无失败、无 race；覆盖率至少 70% |',
           '| `make vet` / `make web-build` | Go 静态检查、TypeScript 类型检查及 Vite 构建 | 命令全部成功；不计浏览器通过 |',
           '| `make check-generated` | 固定 Thrift/Kitex 生成 | `kitex_gen` 与提交一致 |',
           '| `run.py run --profile CI_FAST` | 下表 58 Q；由原生 Ginkgo 报告核验根与完成子例 | 逐键独立收据和原事件全通过 |',
           '| `run.py run --profile CI_SMOKE` | 下表 17 K/B；Ginkgo 每个 It 调度一个原 Python/Playwright 场景 | 原业务断言、Ginkgo 状态、原收据、Pod/镜像身份、浏览器事件及清理全部通过 |',
           '| validate / export / recover | 独立核验、白名单导出、按原 UID 顺序清理 | 核验和导出成功；always 清理有独立成功收据 |', '',
           '## Go UT 与快速场景：' + str(len(go)) + ' 个 It（含 1 个显式启用的诊断夹具）', '',
           '原 `Test...` 字符串作为稳定 selector 保留；只有 suite 入口仍使用 `testing.T`。参数与连续步骤由 Ginkgo `By` 执行，只有完成全部步骤后才记录子例完成标识。共享状态的步骤保持顺序，按子例选择 Q 时运行其所属完整 It。', '']
    out += table(['稳定 selector', '源码', '验证目的', 'CI 选择标准'], go)
    out += ['', '## 验收工具 Host 自测：' + str(len(host)) + ' 项', '',
            '以下全部由 `unittest discover -s scripts/acceptance -p "*test.py"` 收集。统一纳入的理由是保护验收结果的可信度；保持 Python 实现。', '']
    out += table(['用例 selector', '验证目的'], host)
    for profile in ['CI_FAST', 'CI_SMOKE']:
        entries = registry.entries(profile)
        out += ['', '## ' + profile + '：' + str(len(entries)) + ' 个固定键', '',
                '以下逐键登记，重复引用同一根场景仍须分别满足对应必选输入；根场景通过不能替代缺失子例。', '']
        out += table(['稳定键 / 层', '实际 selector / 场景入口', '验证目的', '选取标准'], [
            (e['variantId'] + ' / ' + e['layer'], ', '.join(e['testSelectors']) + '；' + e['runner'],
             e['purpose'] + '；预期：' + e['expected'], rationale(e, profile)) for e in entries])
    out += ['', '## 执行边界与维护', '',
            '普通 Go UT 会跳过 `tests/e2e` 的环境入口与 `TestWorkbenchBrowserFixture` 的显式 Host 夹具；它们没有被算作常规 UT 或真实集群通过。实际 K/B 执行只从 `make acceptance-run` 进入，Ginkgo 调度器保留父 Session 的原 lane、UID 账本和清理责任；Playwright 的 JSON 报告仍作为独立浏览器证据。', '',
            '本文件登记应执行的用例，不表示本次已经执行。实际结果、测试源码与环境失败分别见 `docs/e2e/issue-21-ginkgo-results.md`；远程 CI 以绑定候选 SHA 的 Actions run 为准。', '']
    return '\n'.join(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = render()
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text() != content:
            raise SystemExit('CI 清单已过时，请执行 make ci-cases 并提交文档')
        print('CI 用例清单与源码、profile 和入口一致')
    else:
        DESTINATION.write_text(content)
        print('已生成 docs/acceptance/ci-cases.md')
