"""Build immutable local ZIPs from private Gogs Git; Composer installs the packages.

Gogs web/API archives may need a separate session/token. Git HTTPS credentials
are sufficient here. Mirrors and ZIPs live in ignored .uni/, never in libs/.
"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


def package(entry, root, reference=None):
    root = Path(root).resolve()
    repository = entry['repository']
    url = urlsplit(repository)
    if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
        raise ValueError('Repositorio Gogs deve usar HTTPS sem credenciais na URL.')
    branch = entry.get('branch', 'master')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', branch) or '..' in branch:
        raise ValueError('Branch Gogs invalida.')
    if reference and not re.fullmatch(r'[a-f0-9]{40,64}', reference):
        raise ValueError('Revisao Gogs invalida.')
    name = entry['composer_name']
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*', name):
        raise ValueError('Nome Composer invalido.')
    cache = root / '.uni/packages'
    mirror = root / '.uni/git' / name.replace('/', '-')
    if not cache.resolve().is_relative_to(root) or not mirror.resolve().is_relative_to(root):
        raise ValueError('Cache Composer fora do projeto.')
    cache.mkdir(parents=True, exist_ok=True)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}
    credentials = json.loads(environment.get('COMPOSER_AUTH') or '{}').get('http-basic', {}).get(url.netloc)
    if credentials:
        value = (credentials['username'] + ':' + credentials['password']).encode()
        index = int(environment.get('GIT_CONFIG_COUNT', '0'))
        environment.update(GIT_CONFIG_COUNT=str(index + 1))
        environment['GIT_CONFIG_KEY_' + str(index)] = 'http.' + repository + '.extraHeader'
        environment['GIT_CONFIG_VALUE_' + str(index)] = 'Authorization: Basic ' + base64.b64encode(value).decode()
    def git(*arguments):
        result = subprocess.run(['git', *arguments], cwd=root, env=environment,
                                capture_output=True, text=True, encoding='utf-8', timeout=120)
        if result.returncode: raise ValueError('Falha ao consultar Gogs: ' + result.stderr.strip())
        return result.stdout.strip()
    if not mirror.exists(): git('init', '--bare', str(mirror))
    git('--git-dir=' + str(mirror), 'fetch', '--depth=1', '--', repository,
        reference or 'refs/heads/' + branch)
    revision = git('--git-dir=' + str(mirror), 'rev-parse', 'FETCH_HEAD')
    if reference and revision != reference: raise ValueError('Revisao diferente do lock.')
    manifest = json.loads(git('--git-dir=' + str(mirror), 'show', revision + ':composer.json'))
    if manifest.get('name') != name: raise ValueError('Nome Composer diferente do catalogo: ' + repository)
    filename = name.replace('/', '-') + '-' + revision + '.zip'
    archive = cache / filename
    if not archive.is_file():
        temporary = cache / (filename + '.tmp')
        git('--git-dir=' + str(mirror), 'archive', '--format=zip', '--output=' + str(temporary), revision)
        os.replace(temporary, archive)
    allowed = ('name', 'description', 'type', 'license', 'require', 'require-dev', 'conflict',
               'replace', 'provide', 'suggest', 'autoload', 'autoload-dev', 'extra', 'bin')
    result = {key: manifest[key] for key in allowed if key in manifest}
    result.update(version='dev-' + branch, source={'type': 'git', 'url': repository, 'reference': revision},
                  dist={'type': 'zip', 'url': '.uni/packages/' + filename, 'reference': revision})
    return {'type': 'package', 'package': result}
