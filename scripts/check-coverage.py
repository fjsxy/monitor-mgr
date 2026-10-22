#!/usr/bin/env python3
"""统计手写产品包的语句覆盖率；生成代码、命令参数壳和测试工具不在此 UT 口径内。"""
import argparse
from pathlib import Path


def coverage(path):
    """Go 的多包合并 profile 可能重复同一 block；以同一源码区间的最大计数去重。"""
    blocks = {}
    for line in Path(path).read_text().splitlines()[1:]:
        location, statements, count = line.split()
        if not any('/internal/' + package + '/' in location for package in ['control', 'platform']):
            raise ValueError('覆盖率包含未约定包：' + location)
        n, hits = int(statements), int(count)
        if location in blocks and blocks[location][0] != n:
            raise ValueError('重复 block 的语句数不一致')
        blocks[location] = (n, max(hits, blocks.get(location, (0, 0))[1]))
    total = sum(n for n, _ in blocks.values())
    if not total:
        raise ValueError('覆盖率 profile 为空')
    return 100 * sum(n for n, hits in blocks.values() if hits > 0) / total


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile')
    parser.add_argument('--minimum', type=float, default=70)
    args = parser.parse_args()
    actual = coverage(args.profile)
    print(f'产品语句覆盖率：{actual:.2f}%；门槛：{args.minimum:g}%')
    raise SystemExit(0 if actual >= args.minimum else 1)
