#!/usr/bin/env python3
"""在 job 独立目录安装锁定 Linux amd64 工具，不读取主机凭据或共享 tool cache。"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tarfile
import urllib.parse
import urllib.request
import urllib.error
import zipfile

from capture import capture
from session import atomic_json

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = json.loads(pathlib.Path(__file__).with_name('source-lock.json').read_text())


class BootstrapError(RuntimeError):
    """对外只传播固定错误码。"""
    def __init__(self, code, *, child_exit=None):
        super().__init__(code)
        self.child_exit = child_exit


RUN_STAGES = frozenset(['python-official-setup', 'pyyaml-install', 'python-runtime-metadata',
                        'verify-go', 'verify-node-js', 'verify-kind', 'verify-kubectl', 'npm-ci',
                        'browser-system-deps', 'browser-actual-launch', 'browser-system-versions', 'browser-system-policy'])
STAGES = RUN_STAGES | frozenset(['bootstrap-start', 'host-check', 'prefix-init', 'tool-links', 'python-setup-check',
    'browser-source-check', 'browser-package-check', 'record-private', 'publish-tool-path',
    'download-go', 'extract-go', 'download-node', 'extract-node', 'download-kind', 'install-kind',
    'download-kubectl', 'install-kubectl', 'download-python', 'extract-python', 'download-pip', 'download-pyyaml',
    'download-browser-chromium', 'extract-browser-chromium', 'download-browser-chromium-headless',
    'extract-browser-chromium-headless', 'download-browser-ffmpeg', 'extract-browser-ffmpeg'])
ERROR_CODES = frozenset(['BOOTSTRAP_DIGEST_MISMATCH', 'BOOTSTRAP_SOURCE_NOT_HTTPS', 'BOOTSTRAP_REDIRECT_NOT_HTTPS',
    'BOOTSTRAP_ARCHIVE_UNSAFE', 'BOOTSTRAP_REQUIRES_LINUX_AMD64', 'BOOTSTRAP_REQUIRES_UBUNTU_2404',
    'BOOTSTRAP_PREFIX_MUST_BE_NEW', 'BOOTSTRAP_PYTHON_SETUP_MISSING', 'BOOTSTRAP_PYTHON_RUNTIME_MISMATCH',
    'BOOTSTRAP_TOOL_VERSION_MISMATCH', 'BOOTSTRAP_BROWSER_SOURCE_OVERRIDE', 'BOOTSTRAP_BROWSER_REVISION_MISMATCH',
    'BOOTSTRAP_BROWSER_EXECUTABLE_MISSING', 'BOOTSTRAP_BROWSER_VERSION_MISMATCH', 'BOOTSTRAP_APT_POLICY_INCOMPLETE',
    'BOOTSTRAP_REPORT_PATH_INVALID', 'BOOTSTRAP_STAGE_INVALID', 'BOOTSTRAP_PYTHON_LOCATION_MISMATCH']) | frozenset(
        'BOOTSTRAP_STEP_FAILED_' + stage.upper().replace('-', '_') for stage in RUN_STAGES)


def safe_failure(error):
    """关键：异常消息、动态类名和 HTTP reason/body 可含凭据，只投影固定分类与整数状态。"""
    result = dict(errorCode='BOOTSTRAP_UNEXPECTED', exceptionClass='Exception', httpStatus=None, childExit=None)
    if type(error) is BootstrapError:
        result['exceptionClass'] = 'BootstrapError'
        if error.args and type(error.args[0]) is str and error.args[0] in ERROR_CODES:
            result['errorCode'] = error.args[0]
            if type(error.child_exit) is int and -255 <= error.child_exit <= 255:
                result['childExit'] = error.child_exit
    else:
        for kind, name, code in [(urllib.error.HTTPError, 'HTTPError', 'BOOTSTRAP_HTTP_ERROR'),
                (urllib.error.URLError, 'URLError', 'BOOTSTRAP_DOWNLOAD_ERROR'),
                (OSError, 'OSError', 'BOOTSTRAP_OS_ERROR'), (ValueError, 'ValueError', 'BOOTSTRAP_VALUE_ERROR'),
                (KeyError, 'KeyError', 'BOOTSTRAP_KEY_ERROR'), (KeyboardInterrupt, 'KeyboardInterrupt', 'BOOTSTRAP_INTERRUPTED'),
                (SystemExit, 'SystemExit', 'BOOTSTRAP_INTERRUPTED')]:
            if isinstance(error, kind):
                result.update(errorCode=code, exceptionClass=name)
                if kind is urllib.error.HTTPError and type(error.code) is int and 100 <= error.code <= 599:
                    result['httpStatus'] = error.code
                break
    return result


class BootstrapDiagnostic:
    """bootstrap 独立公共摘要；先 seed 再进入实际步骤，不能冒充正式验收 index。"""
    def __init__(self, path, *, browsers):
        self.path = pathlib.Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise BootstrapError('BOOTSTRAP_REPORT_PATH_INVALID')
        self.value = dict(schemaVersion=1, kind='bootstrap-diagnostic', productGate=False, state='pending',
                          stage='bootstrap-start', errorCode=None, exceptionClass=None, httpStatus=None, childExit=None,
                          browsers=bool(browsers), sourceLockSHA256=hashlib.sha256(
                              pathlib.Path(__file__).with_name('source-lock.json').read_bytes()).hexdigest())

    def write(self):
        atomic_json(self.path, self.value)

    def enter(self, stage):
        if stage not in STAGES:
            raise BootstrapError('BOOTSTRAP_STAGE_INVALID')
        self.value.update(stage=stage, state='running')
        self.write()

    def finish(self, error):
        self.value.update(state='failed' if error is not None else 'completed')
        if error is not None:
            self.value.update(safe_failure(error))
        self.write()


def verify_file(path, digest):
    """下载成功与官方摘要匹配是不同事实，必须实际读取完整字节。"""
    if not re.fullmatch('[a-f0-9]{64}', digest) or hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() != digest:
        raise BootstrapError('BOOTSTRAP_DIGEST_MISMATCH')
    return pathlib.Path(path).stat().st_size


def download(url, destination, digest=None):
    """下载固定 HTTPS URL；浏览器无官方摘要时只记录本次观测 hash。"""
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise BootstrapError('BOOTSTRAP_SOURCE_NOT_HTTPS')
    destination = pathlib.Path(destination)
    with urllib.request.urlopen(url, timeout=180) as response:
        if urllib.parse.urlsplit(response.url).scheme != 'https':
            raise BootstrapError('BOOTSTRAP_REDIRECT_NOT_HTTPS')
        with open(destination, 'xb') as stream:
            shutil.copyfileobj(response, stream)
    if digest:
        verify_file(destination, digest)
    return {'url': url, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'bytes': destination.stat().st_size, 'authority': 'official' if digest else 'observed_download_only'}


def extract_tar(archive, destination):
    """仅解压安全 data 成员，绝不接受绝对路径、设备或越界链接。"""
    destination = pathlib.Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive) as stream:
            stream.extractall(destination, filter='data')
    except (tarfile.TarError, OSError):
        raise BootstrapError('BOOTSTRAP_ARCHIVE_UNSAFE') from None


def _zip(archive, destination):
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as stream:
        for info in stream.infolist():
            target = destination / info.filename
            if info.filename.startswith('/') or not target.resolve().is_relative_to(destination.resolve()):
                raise BootstrapError('BOOTSTRAP_ARCHIVE_UNSAFE')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BootstrapError('BOOTSTRAP_ARCHIVE_UNSAFE')
        stream.extractall(destination)
        for info in stream.infolist():
            target = destination / info.filename
            if target.is_file():
                target.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)


def apt_policy_summary(text):
    """只保存包/版本与 origin host/suite/component，丢弃原 URL 的认证、query 和路径。"""
    result = []
    current = None
    for line in text.splitlines():
        if re.fullmatch(r'[a-z0-9.+-]+(?::[a-z0-9_-]+)?:', line):
            current = {'package': line[:-1], 'installed': None, 'candidate': None, 'origins': []}
            result.append(current)
            continue
        if current is None:
            continue
        for label in ('Installed', 'Candidate'):
            match = re.fullmatch(r'\s*' + label + r':\s*([a-zA-Z0-9.+:~_-]+)\s*', line)
            if match:
                current[label.lower()] = match[1]
        tokens = line.split()
        for number, token in enumerate(tokens[:-1]):
            if token.startswith(('https://', 'http://')):
                host = urllib.parse.urlsplit(token).hostname
                suite_component = tokens[number + 1].split('/', 1)
                if host and all(re.fullmatch('[a-zA-Z0-9._-]+', item) for item in suite_component):
                    origin = {'host': host, 'suite': suite_component[0], 'component': suite_component[1] if len(suite_component) > 1 else None}
                    if origin not in current['origins']:
                        current['origins'].append(origin)
    return result


def python_location_script():
    """只读实际进程映射和标准库来源；编译期 LIBDIR 不能充当已加载库证明。"""
    return '''import sys,sysconfig,encodings,pathlib,hashlib,json
loaded=set()
for line in pathlib.Path('/proc/self/maps').read_text().splitlines():
    fields=line.split(maxsplit=5)
    if len(fields)==6 and pathlib.Path(fields[5]).name=='libpython3.14.so.1.0':
        loaded.add(str(pathlib.Path(fields[5]).resolve()))
metadata={'prefix':str(pathlib.Path(sys.prefix).resolve()),'basePrefix':str(pathlib.Path(sys.base_prefix).resolve()),
          'stdlib':str(pathlib.Path(sysconfig.get_path('stdlib')).resolve()),
          'encodings':str(pathlib.Path(encodings.__file__).resolve()),
          'loadedLibpython':[{'path':path,'sha256':hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()} for path in sorted(loaded)]}
'''


def verify_python_location(metadata, python_root, distribution_digest):
    """关键：解释器、stdlib和实际加载库全部绑定新cache，库字节再绑定已校验的官方分发。"""
    python_root = pathlib.Path(python_root).resolve()
    expected = dict(prefix=str(python_root), basePrefix=str(python_root), stdlib=str(python_root / 'lib/python3.14'),
                    encodings=str(python_root / 'lib/python3.14/encodings/__init__.py'),
                    loadedLibpython=[dict(path=str(python_root / 'lib/libpython3.14.so.1.0'), sha256=distribution_digest)])
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise BootstrapError('BOOTSTRAP_PYTHON_LOCATION_MISMATCH')


def install(prefix, record_path, *, browsers=False, diagnostic=None):
    """首次安装在全新 prefix，安装动作/原输出全部私有捕获。"""
    def mark(stage):
        if diagnostic is not None:
            diagnostic.enter(stage)
    mark('host-check')
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise BootstrapError('BOOTSTRAP_REQUIRES_LINUX_AMD64')
    os_release = pathlib.Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in os_release or 'VERSION_ID="24.04"' not in os_release:
        raise BootstrapError('BOOTSTRAP_REQUIRES_UBUNTU_2404')
    mark('prefix-init')
    prefix = pathlib.Path(prefix)
    if not prefix.is_absolute() or prefix.exists():
        raise BootstrapError('BOOTSTRAP_PREFIX_MUST_BE_NEW')
    prefix.mkdir(parents=True, mode=0o700)
    bin_dir, downloads, wheels = prefix / 'bin', prefix / 'downloads', prefix / 'wheels'
    for directory in (bin_dir, downloads, wheels):
        directory.mkdir(mode=0o700)
    record = {'schemaVersion': 1, 'platform': 'linux/amd64', 'os': 'ubuntu24.04',
              'host': {'system': platform.system(), 'machine': platform.machine()}, 'tools': {}, 'browsers': None,
              'sourceLockSHA256': hashlib.sha256(pathlib.Path(__file__).with_name('source-lock.json').read_bytes()).hexdigest()}
    environment = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], GOTOOLCHAIN='local')
    # 关键：禁用用户 pip 配置和远端 index；官方 setup.sh 的升级只看到固定 pip wheel。
    environment.update(PIP_CONFIG_FILE='/dev/null', PIP_NO_INDEX='1', PIP_FIND_LINKS=str(wheels),
                       AGENT_TOOLSDIRECTORY=str(prefix / 'toolcache'), RUNNER_TOOL_CACHE=str(prefix / 'toolcache'))
    python_root = prefix / 'toolcache/Python/3.14.7/x64'
    # 关键：官方ELF的RUNPATH写死默认cache；第一次setup启动前替换外来库路径，避免借用runner预装lib。
    environment['LD_LIBRARY_PATH'] = str(python_root / 'lib')
    def run(argv, label, **kwargs):
        mark(label)
        result = capture([str(item) for item in argv], private_root=str(prefix / 'raw'), label=label,
                         env=environment, publish=False, **kwargs)
        if result.returncode:
            raise BootstrapError('BOOTSTRAP_STEP_FAILED_' + label.upper().replace('-', '_'), child_exit=result.returncode)
        return result
    tool_ids = {'Go': 'go', 'Node.js': 'node', 'kind': 'kind', 'kubectl': 'kubectl', 'Python': 'python', 'pip': 'pip', 'PyYAML': 'pyyaml'}
    for name in ('Go', 'Node.js', 'kind', 'kubectl', 'Python', 'pip', 'PyYAML'):
        spec = LOCK['tools'][name]
        path = (wheels if name in ('pip', 'PyYAML') else downloads) / urllib.parse.urlsplit(spec['url']).path.rsplit('/', 1)[1]
        mark('download-' + tool_ids[name])
        record['tools'][name] = dict(version=spec['version'], **download(spec['url'], path, spec['sha256']))
        if name in ('Go', 'Node.js'):
            mark('extract-' + tool_ids[name])
            extract_tar(path, prefix)
        elif name in ('kind', 'kubectl'):
            mark('install-' + tool_ids[name])
            shutil.copyfile(path, bin_dir / name)
            (bin_dir / name).chmod(0o755)
        elif name == 'Python':
            mark('extract-python')
            extract_tar(path, prefix / 'python-distribution')
    mark('tool-links')
    for executable in ('go', 'gofmt'):
        (bin_dir / executable).symlink_to(prefix / 'go' / 'bin' / executable)
    for executable in ('node', 'npm', 'npx', 'corepack'):
        source = prefix / 'node-v26.4.0-linux-x64' / 'bin' / executable
        if source.exists():
            (bin_dir / executable).symlink_to(source)
    mark('python-setup-check')
    setup = prefix / 'python-distribution' / 'setup.sh'
    if not setup.is_file():
        raise BootstrapError('BOOTSTRAP_PYTHON_SETUP_MISSING')
    record['tools']['Python']['actualSetupSHA256'] = hashlib.sha256(setup.read_bytes()).hexdigest()
    distribution_lib_digest = hashlib.sha256((setup.parent / 'lib/libpython3.14.so.1.0').read_bytes()).hexdigest()
    record['tools']['Python']['distributionLibSHA256'] = distribution_lib_digest
    # 脚本来自上面已校验的整个分发包；全新 toolcache 避免命中 runner 预装 Python。
    run(['bash', str(setup)], 'python-official-setup', cwd=str(setup.parent), timeout=180)
    python_bin = python_root / 'bin/python3.14'
    (bin_dir / 'python').symlink_to(python_bin)
    (bin_dir / 'python3').symlink_to(python_bin)
    requirements = prefix / 'requirements-linux.txt'
    requirements.write_text('PyYAML==6.0.3 --hash=sha256:' + LOCK['tools']['PyYAML']['sha256'] + '\n')
    run([python_bin, '-m', 'pip', 'install', '--force-reinstall', '--no-deps', '--only-binary=:all:', '--require-hashes', '-r', requirements], 'pyyaml-install', timeout=180)
    metadata_script = python_location_script() + 'import platform,importlib.metadata as m;metadata.update(version=platform.python_version(),SOABI=sysconfig.get_config_var("SOABI"),gilDisabled=sysconfig.get_config_var("Py_GIL_DISABLED"),pip=m.version("pip"),PyYAML=m.version("PyYAML"));print(json.dumps(metadata))'
    metadata = json.loads(run([python_bin, '-I', '-c', metadata_script], 'python-runtime-metadata').stdout)
    if metadata['version'] != '3.14.7' or metadata['gilDisabled'] or metadata['pip'] != '26.2.1' or metadata['PyYAML'] != '6.0.3':
        raise BootstrapError('BOOTSTRAP_PYTHON_RUNTIME_MISMATCH')
    verify_python_location(metadata, python_root, distribution_lib_digest)
    record['pythonRuntime'] = metadata
    for name, argv, expected in [('Go', ['go', 'version'], b'go1.27.1 linux/amd64'),
                                 ('Node.js', ['node', '--version'], b'v26.4.0'),
                                 ('kind', ['kind', 'version'], b'v0.33.0')]:
        observed = run(argv, 'verify-' + name.lower().replace('.', '-')).stdout.strip()
        if expected not in observed:
            raise BootstrapError('BOOTSTRAP_TOOL_VERSION_MISMATCH')
        record['tools'][name]['actualVersion'] = observed.decode()
    kubectl = json.loads(run(['kubectl', 'version', '--client=true', '-o', 'json'], 'verify-kubectl').stdout)
    if kubectl['clientVersion']['gitVersion'] != 'v1.28.15':
        raise BootstrapError('BOOTSTRAP_TOOL_VERSION_MISMATCH')
    record['tools']['kubectl']['actualVersion'] = 'v1.28.15'
    run(['npm', '--prefix', str(ROOT / 'web'), 'ci'], 'npm-ci', timeout=300)
    if browsers:
        mark('browser-source-check')
        for key in ('PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST', 'PLAYWRIGHT_DOWNLOAD_HOST'):
            if environment.get(key):
                raise BootstrapError('BOOTSTRAP_BROWSER_SOURCE_OVERRIDE')
        browser_root = prefix / 'browsers'
        browser_root.mkdir()
        environment['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_root)
        mark('browser-package-check')
        package = json.loads((ROOT / 'web/node_modules/playwright-core/package.json').read_text())
        definitions = json.loads((ROOT / 'web/node_modules/playwright-core/browsers.json').read_text())['browsers']
        chromium = next(item for item in definitions if item['name'] == 'chromium')
        if package['version'] != '1.64.1' or str(chromium['revision']) != '1248' or chromium['browserVersion'] != '156.0.8078.4':
            raise BootstrapError('BOOTSTRAP_BROWSER_REVISION_MISMATCH')
        cli = ROOT / 'web/node_modules/.bin/playwright'
        run([cli, 'install-deps', 'chromium'], 'browser-system-deps', timeout=300)
        actual_archives = []
        directories = ['chromium-1248', 'chromium_headless_shell-1248', 'ffmpeg-1013']
        executable_paths = ['chrome-linux64/chrome', 'chrome-headless-shell-linux64/chrome-headless-shell', 'ffmpeg-linux']
        for spec, directory, executable_path in zip(LOCK['playwright']['archives'], directories, executable_paths):
            archive = downloads / pathlib.Path(urllib.parse.urlsplit(spec['url']).path).name
            browser_id = {'chromium': 'chromium', 'chromium_headless': 'chromium-headless', 'ffmpeg': 'ffmpeg'}[spec['name']]
            mark('download-browser-' + browser_id)
            actual = download(spec['url'], archive)
            destination = browser_root / directory
            mark('extract-browser-' + browser_id)
            _zip(archive, destination)
            executable = destination / executable_path
            if not executable.is_file():
                raise BootstrapError('BOOTSTRAP_BROWSER_EXECUTABLE_MISSING')
            executable.chmod(0o755)
            # 固定 Playwright registry 的安装完成标记；实际 launch 再确认解析到这些文件。
            (destination / 'INSTALLATION_COMPLETE').touch()
            actual.update(name=spec['name'], officialSHA256=None, executableSHA256=hashlib.sha256(executable.read_bytes()).hexdigest())
            actual_archives.append(actual)
        script = 'const{chromium}=require(process.argv[1]);(async()=>{const b=await chromium.launch({headless:true});console.log(JSON.stringify({version:b.version(),executable:chromium.executablePath()}));await b.close()})().catch(()=>process.exit(1))'
        browser = json.loads(run(['node', '-e', script, str(ROOT / 'web/node_modules/playwright')], 'browser-actual-launch', timeout=60).stdout)
        if browser['version'] != '156.0.8078.4':
            raise BootstrapError('BOOTSTRAP_BROWSER_VERSION_MISMATCH')
        versions = run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n', *LOCK['playwright']['systemPackages']], 'browser-system-versions').stdout.decode()
        policy = run(['apt-cache', 'policy', *LOCK['playwright']['systemPackages']], 'browser-system-policy').stdout.decode()
        apt_policy = apt_policy_summary(policy)
        if len(apt_policy) != len(LOCK['playwright']['systemPackages']) or any(not item['installed'] or not item['candidate'] for item in apt_policy):
            raise BootstrapError('BOOTSTRAP_APT_POLICY_INCOMPLETE')
        apt_origins = sorted({origin['host'] for item in apt_policy for origin in item['origins']})
        record['browsers'] = {'playwrightVersion': '1.64.1', 'revision': '1248', 'version': browser['version'],
                              'archives': actual_archives, 'aptPackageVersions': versions.splitlines(),
                              'aptOriginHosts': apt_origins, 'aptPolicy': apt_policy, 'aptSnapshotPinned': False,
                              'digestBoundary': 'archive hashes are observed in this job; no independent upstream archive digest'}
    mark('record-private')
    record_path = pathlib.Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    mark('publish-tool-path')
    for key, value in [('GITHUB_PATH', str(bin_dir)), ('GITHUB_ENV', 'PLAYWRIGHT_BROWSERS_PATH=' + str(prefix / 'browsers')),
                       ('GITHUB_ENV', 'LD_LIBRARY_PATH=' + str(python_root / 'lib'))]:
        if os.environ.get(key):
            with open(os.environ[key], 'a') as stream:
                stream.write(value + '\n')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description='安装官方摘要固定的 Linux amd64 工具')
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--record', required=True)
    parser.add_argument('--browsers', action='store_true')
    parser.add_argument('--public-report', required=True)
    args = parser.parse_args(argv)
    diagnostic = None;failure = None;report_failed = False
    try:
        diagnostic = BootstrapDiagnostic(args.public_report, browsers=args.browsers)
        diagnostic.write()
        install(args.prefix, args.record, browsers=args.browsers, diagnostic=diagnostic)
    except BaseException as error:
        failure = error
    finally:
        if diagnostic is not None:
            try:diagnostic.finish(failure)
            except BaseException:report_failed = True
    code = 1 if failure is not None or report_failed else 0
    result = dict(id='bootstrap', exit=code)
    if failure is not None:result.update(safe_failure(failure))
    if report_failed:result['reportErrorCode'] = 'BOOTSTRAP_REPORT_WRITE_FAILED'
    print(json.dumps(result))
    return code


if __name__ == '__main__':
    sys.exit(main())
