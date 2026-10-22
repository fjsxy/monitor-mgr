#!/usr/bin/env python3
"""校验当前固定目录与实际 UT/E2E 注册；不执行产品测试。"""
import runpy
from pathlib import Path
namespace=runpy.run_path(str(Path(__file__).with_name('ci-cases.py')))
namespace['document']()
print('固定目录一致：18 UT / 10 E2E；元数据校验不代表运行通过')
