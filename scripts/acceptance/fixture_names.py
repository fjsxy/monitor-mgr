"""测试 namespace 的有界派生名；完整 run 身份仍由标签和原 namespace UID 保存。"""
import hashlib
import re


def namespace_name(value):
    """关键：长前缀保留稳定散列，避免简单截断把两个独占 fixture 合成同名资源。"""
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', value):
        raise ValueError('FIXTURE_NAMESPACE_INVALID')
    if len(value) <= 63:
        return value
    return value[:50].rstrip('-') + '-' + hashlib.sha256(value.encode()).hexdigest()[:12]
