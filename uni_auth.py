"""Reuse configured local Git credentials in memory; never write auth into a project."""
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def composer_environment(root):
    environment = os.environ.copy()
    if environment.get('COMPOSER_AUTH') or Path('/.dockerenv').exists(): return environment
    manifest = Path(root) / 'composer.json'
    if not manifest.is_file(): return environment
    libraries = json.loads(manifest.read_text(encoding='utf-8-sig')).get('extra', {}).get('uni', {}).get('libraries', {})
    basic = {}
    for entry in libraries.values():
        if entry.get('provider') != 'gogs': continue
        url = urlsplit(entry['repository'])
        if url.scheme != 'https' or url.username or url.password: continue
        if url.netloc in basic: continue
        result = subprocess.run(['git', 'credential', 'fill'], cwd=root,
                                input=f'protocol=https\nhost={url.netloc}\npath={url.path.lstrip("/")}\n\n',
                                capture_output=True, text=True, timeout=30,
                                env={**environment, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'})
        if result.returncode: continue
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if values.get('username') and values.get('password'):
            basic[url.netloc] = {'username': values['username'], 'password': values['password']}
    if basic: environment['COMPOSER_AUTH'] = json.dumps({'http-basic': basic})
    return environment
