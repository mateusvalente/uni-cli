"""Workspace local, selecao de ambiente e ciclo de vida dos containers."""
import json
import os
import re
import shutil
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path

from uni_packages import read_json, write_json
from uni_runtime import CLI, run_tool

LOCAL = CLI / '.local.json'
CATALOG = CLI / 'libs_projects.json'
IGNORE = {'.git', '.uni', '.tmp', '.venv', 'vendor', 'libs', 'storage', 'node_modules', 'tests', 'uni-cli'}


def run(command, cwd=None, capture=True, environment=None):
    result = subprocess.run(list(map(str, command)), cwd=cwd, text=True, encoding='utf-8',
                            capture_output=capture, env={**(environment or os.environ), 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip() if capture else 'Consulte a saida acima.'
        raise ValueError(f"Falha em {command[0]}: {detail}")
    return result.stdout.strip() if capture else ''


def slug(value):
    if not re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', value):
        raise ValueError('Nome invalido: ' + value)
    return value


def git_root(root):
    root = Path(root).resolve()
    if Path(run(['git', 'rev-parse', '--show-toplevel'], root)).resolve() != root:
        raise ValueError('A pasta precisa de um repositorio Git proprio: ' + str(root))
    return root


def doctor():
    for tool in ('git', 'docker'):
        if not shutil.which(tool):
            raise ValueError(f'Instale {tool} e disponibilize no PATH.')
    run(['git', '--version'])
    run(['docker', 'compose', 'version'])
    if run(['docker', 'info', '--format', '{{.OSType}}']) != 'linux':
        raise ValueError('Configure o Docker para containers Linux.')
    print('Git, Docker e Compose disponiveis.')


def workspace_root(start=None):
    current = Path(start or Path.cwd()).resolve()
    for path in (current, *current.parents):
        if (path / '.uni/workspace.json').is_file():
            return path
    if LOCAL.is_file():
        path = Path(read_json(LOCAL)['workspace'])
        if (path / '.uni/workspace.json').is_file():
            return path
    raise ValueError('Workspace nao inicializado. Execute uni start na pasta que contem os projetos.')


class Workspace:
    def __init__(self, root=None, catalog=CATALOG):
        self.root = Path(root).resolve() if root else workspace_root()
        self.file = self.root / '.uni/workspace.json'
        self.catalog_file = Path(catalog)
        self.catalog = read_json(catalog)
        self.state = read_json(self.file) if self.file.exists() else {'projects': {}, 'environments': {}, 'active': None, 'selected': None}

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.file, self.state)

    def adopt_running(self):
        """Reconhece um ambiente ja iniciado manualmente sem reinicia-lo."""
        if self.state.get('active') or not shutil.which('docker'): return
        running = []
        for environment in self.state['environments']:
            try:
                names = self.members(environment)
                for name in names:
                    folder = self.docker_path(name)
                    branch = self.catalog['projects'][name]['docker']['branch']
                    if run(['git', 'branch', '--show-current'], folder) != branch: break
                    if not (folder / '.env').is_file(): break
                    services = self.compose(name, ['ps', '--status', 'running', '--services'], capture=True).splitlines()
                    if not {'php', 'nginx'}.issubset(services): break
                else: running.append(environment)
            except (ValueError, OSError, KeyError): continue
        if len(running) == 1:
            self.state['active'] = running[0]
            self.state['selected'] = self.state['environments'][running[0]].get('front', self.members(running[0])[0])
            self.save()

    @contextmanager
    def locked(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with (self.file.parent / 'operation.lock').open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
                try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc: raise ValueError('Outra operacao de ambiente esta em andamento.') from exc
            else:
                import fcntl
                try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc: raise ValueError('Outra operacao de ambiente esta em andamento.') from exc
            try: yield
            finally:
                if os.name == 'nt':
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(stream, fcntl.LOCK_UN)

    def scan(self):
        projects = {}
        composer_names = set()
        for folder, directories, files in os.walk(self.root):
            directories[:] = [d for d in directories if d not in IGNORE and not d.startswith('.')
                              and not (Path(folder) / d).is_symlink()]
            if 'composer.json' not in files:
                continue
            root = Path(folder).resolve()
            manifest = read_json(root / 'composer.json')
            if manifest.get('type', 'project') != 'project':
                directories[:] = []; continue
            composer_name = manifest.get('name')
            if not isinstance(composer_name, str) or '/' not in composer_name:
                raise ValueError('Projeto sem name Composer: ' + str(root))
            entry_pair = next(((k, v) for k, v in self.catalog['projects'].items() if v['composer_name'] == composer_name), None)
            name = entry_pair[0] if entry_pair else composer_name.split('/')[-1]
            slug(name)
            if name in projects or composer_name in composer_names:
                raise ValueError('Projeto duplicado no workspace: ' + name)
            composer_names.add(composer_name)
            config = manifest.get('extra', {}).get('uni', {})
            entry = entry_pair[1] if entry_pair else {}
            role = entry.get('role', config.get('role', 'back' if name.endswith('-back') else 'front'))
            if role not in ('front', 'back'):
                raise ValueError('Papel invalido no projeto ' + name)
            environment = entry.get('environment', config.get('environment', re.sub(r'-(front|back)$', '', name)))
            slug(environment)
            projects[name] = {'path': str(root), 'composer_name': composer_name, 'role': role, 'environment': environment}
            directories[:] = []
        environments = {}
        for name, entry in projects.items():
            roles = environments.setdefault(entry['environment'], {})
            if entry['role'] in roles:
                raise ValueError('Dois projetos com o mesmo papel no ambiente: ' + entry['environment'])
            roles[entry['role']] = name
        # Associacao explicita prevalece sobre convencoes de nomes.
        for name, entry in self.catalog['projects'].items():
            backend = entry.get('related', {}).get('backend')
            if name in projects and backend:
                if backend not in projects:
                    continue  # use verificara a ausencia antes de parar o ambiente atual.
                env = projects[name]['environment']
                other = projects[backend]['environment']
                if other != env:
                    environments[other].pop('back', None)
                    if not environments[other]: del environments[other]
                environments[env]['back'] = backend
                projects[backend]['environment'] = env
        self.state['projects'], self.state['environments'] = projects, environments
        self.save()
        write_json(LOCAL, {'workspace': str(self.root)})
        return projects

    def selected_path(self):
        name = self.state.get('selected')
        if name not in self.state['projects']:
            raise ValueError('Selecione um ambiente com uni use NOME.')
        path = Path(self.state['projects'][name]['path'])
        if not (path / 'composer.json').is_file():
            raise ValueError('Projeto movido ou ausente. Execute uni start novamente.')
        return path

    def select(self, role):
        roles = self.state['environments'].get(self.state.get('active'), {})
        if role not in roles:
            raise ValueError('O ambiente ativo nao possui ' + role + '.')
        self.state['selected'] = roles[role]
        self.save()
        print('Projeto selecionado: ' + roles[role])
        print(self.selected_path())

    def members(self, environment):
        if environment not in self.state['environments']:
            raise ValueError('Ambiente nao encontrado: ' + str(environment))
        roles = self.state['environments'][environment]
        front = roles.get('front')
        backend = self.catalog['projects'].get(front, {}).get('related', {}).get('backend')
        if backend and roles.get('back') != backend:
            raise ValueError('Backend associado ausente. Clone/registre o projeto ' + backend + ' e execute uni start.')
        return [roles[r] for r in ('back', 'front') if r in roles]

    def docker_path(self, name):
        role = self.state['projects'][name]['role']
        return self.root / ('docker-' + role)

    def compose(self, name, arguments, capture=False):
        folder = self.docker_path(name)
        from uni_auth import composer_environment
        environment = composer_environment(Path(self.state['projects'][name]['path']))
        return run(['docker', 'compose', '--project-directory', folder, '--env-file', folder / '.env', *arguments],
                   capture=capture, environment=environment)

    def prepare(self, name):
        from uni_projects import repository_identity
        entry = self.catalog['projects'].get(name, {})
        docker = entry.get('docker')
        if not docker:
            raise ValueError('Projeto sem branch Docker registrada: ' + name)
        root = self.docker_path(name)
        if not root.exists():
            run(['git', 'clone', '--branch', docker['branch'], '--', docker['repository'], root])
        git_root(root)
        if repository_identity(run(['git', 'remote', 'get-url', 'origin'], root)) != repository_identity(docker['repository']):
            raise ValueError('Remote Docker inesperado: ' + str(root))
        if run(['git', 'status', '--porcelain'], root):
            raise ValueError('Docker com alteracoes locais; commit ou stash antes de trocar: ' + str(root))
        run(['git', 'check-ref-format', '--branch', docker['branch']], root)
        run(['git', 'fetch', 'origin', 'refs/heads/' + docker['branch'] + ':refs/remotes/origin/' + docker['branch']], root)
        run(['git', 'rev-parse', '--verify', 'origin/' + docker['branch']], root)

    def write_env(self, name):
        project = self.state['projects'][name]
        root = Path(project['path'])
        port = read_json(root / 'composer.json').get('extra', {}).get('uni', {}).get('docker', {}).get('port', 8082 if project['role'] == 'back' else 8080)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Porta invalida: ' + name)
        values = {'UNI_PROJECT': project['composer_name'], 'COMPOSE_PROJECT_NAME': name,
                  'PROJECT_PATH': root.as_posix(), 'UNI_CLI_PATH': CLI.as_posix(), 'APP_PORT': str(port),
                  'ENVIRONMENT_NETWORK': project['environment'] + '-network'}
        if any('\n' in v or "'" in v for v in values.values()):
            raise ValueError('Caminho ou identificador invalido para o .env.')
        (self.docker_path(name) / '.env').write_text(''.join(f"{k}='{v}'\n" for k, v in values.items()), encoding='utf-8')
        return port

    def switch(self, environment):
        with self.locked():
            names = self.members(environment)
            previous = self.state.get('active')
            old_names = self.members(previous) if previous else []
            # Preflight em ambos os repositorios antes de derrubar qualquer servico.
            for name in names: self.prepare(name)
            for name in old_names:
                if run(['git', 'status', '--porcelain'], self.docker_path(name)):
                    raise ValueError('Docker ativo com alteracoes locais: ' + name)
            snapshots = {}
            for name in dict.fromkeys([*old_names, *names]):
                folder = self.docker_path(name)
                if folder in snapshots: continue
                snapshots[folder] = (run(['git', 'rev-parse', 'HEAD'], folder),
                                     run(['git', 'branch', '--show-current'], folder),
                                     (folder / '.env').read_bytes() if (folder / '.env').exists() else None)
            started = []
            try:
                for name in reversed(old_names): self.compose(name, ['down'])
                ports = set()
                for name in names:
                    folder = self.docker_path(name)
                    branch = self.catalog['projects'][name]['docker']['branch']
                    exists = subprocess.run(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch], cwd=folder)
                    run(['git', 'switch', branch] if exists.returncode == 0 else ['git', 'switch', '-c', branch, '--track', 'origin/' + branch], folder)
                    run(['git', 'merge', '--ff-only', 'origin/' + branch], folder)
                    port = self.write_env(name)
                    if port in ports: raise ValueError('Porta duplicada no ambiente: ' + str(port))
                    ports.add(port)
                    with socket.socket() as probe:
                        try: probe.bind(('127.0.0.1', port))
                        except OSError as exc: raise ValueError('Porta em uso: ' + str(port)) from exc
                    self.compose(name, ['config', '--quiet'])
                for name in names:
                    started.append(name)
                    self.compose(name, ['up', '-d', '--wait', '--wait-timeout', '180'])
                    self.compose(name, ['exec', '-T', 'php', 'php', '-v'], capture=True)
                    self.compose(name, ['exec', '-T', 'nginx', 'nginx', '-t'], capture=True)
            except Exception as error:
                failures = []
                for name in reversed(started):
                    try: self.compose(name, ['down'])
                    except Exception as restore: failures.append(str(restore))
                for folder, (commit, branch, env) in snapshots.items():
                    try:
                        run(['git', 'switch', '--detach', commit], folder)
                        if branch and run(['git', 'rev-parse', branch], folder) == commit:
                            run(['git', 'switch', branch], folder)
                        if env is None: (folder / '.env').unlink(missing_ok=True)
                        else: (folder / '.env').write_bytes(env)
                    except Exception as restore: failures.append(str(restore))
                for name in old_names:
                    try: self.compose(name, ['up', '-d', '--wait', '--wait-timeout', '60'])
                    except Exception as restore: failures.append(str(restore))
                if failures:
                    self.state['recovery_required'] = failures; self.save()
                raise ValueError(str(error) + (' Recuperacao incompleta: ' + '; '.join(failures) if failures else ' Ambiente anterior restaurado.')) from error
            self.state.update(active=environment, selected=self.state['environments'][environment].get('front', names[0]))
            self.state.pop('recovery_required', None)
            self.save()
        print('Ambiente ativo: ' + environment)

    def operate(self, operation):
        names = self.members(self.state.get('active'))
        with self.locked():
            for name in reversed(names) if operation == 'down' else names:
                self.compose(name, ['down'] if operation == 'down' else ['up', '-d', '--wait', '--wait-timeout', '60'])

    def status(self):
        print('Workspace: ' + str(self.root))
        print('Ambiente: ' + str(self.state.get('active') or 'nenhum'))
        print('Projeto de trabalho: ' + str(self.state.get('selected') or 'nenhum'))
        for environment, roles in self.state['environments'].items():
            print(environment + ': ' + ', '.join(f'{role}={name}' for role, name in roles.items()))
        if self.state.get('recovery_required'): print('RECUPERACAO PENDENTE: ' + str(self.state['recovery_required']))
        if self.state.get('active'):
            for name in self.members(self.state['active']):
                print(name + ' | branch ' + run(['git', 'branch', '--show-current'], self.docker_path(name)))
                self.compose(name, ['ps'])

    def editor(self, choice=None):
        if choice is None:
            if not sys_stdin_tty(): return
            print('1. Nao alterar janelas (padrao)\n2. Abrir novas janelas\n3. Reutilizar a janela atual do VS Code')
            try:
                choice = input('VS Code [1]: ').strip() or '1'
            except EOFError:
                return
        if choice in ('1', 'none'): return
        if choice not in ('2', '3', 'new', 'reuse'): raise ValueError('Opcao de editor invalida.')
        code = shutil.which('code')
        if not code: raise ValueError('VS Code nao encontrado no PATH. O ambiente ja foi selecionado.')
        names = self.members(self.state['active'])
        for index, name in enumerate(names):
            reuse = choice in ('3', 'reuse') and index == 0 and os.environ.get('TERM_PROGRAM') == 'vscode'
            subprocess.run([code, '--reuse-window' if reuse else '--new-window', self.state['projects'][name]['path']], check=True)


def sys_stdin_tty():
    import sys
    return sys.stdin.isatty()


def current_project():
    # Projetos avulsos (inclusive fixtures) continuam independentes do workspace.
    current = Path.cwd().resolve()
    for path in (current, *current.parents):
        if (path / 'composer.json').is_file():
            try:
                ws = Workspace()
                if str(path) in [p['path'] for p in ws.state['projects'].values()] and ws.state.get('selected'):
                    return ws.selected_path()
            except ValueError: pass
            return path
    return Workspace().selected_path()


def clone_project(ws, name, no_install=False):
    from uni_projects import repository_identity
    import tempfile
    if name not in ws.catalog['projects']: raise ValueError('Projeto nao cadastrado: ' + name)
    entry = ws.catalog['projects'][name]
    repository_identity(entry['repository'])
    root = ws.root / slug(entry['root_name'])
    if name in ws.state['projects'] or root.exists(): raise ValueError('Projeto ja existe localmente: ' + name)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix='.clone-') as temporary:
        prepared = Path(temporary) / 'project'
        command = ['git', 'clone']
        if entry.get('branch'): command += ['--branch', entry['branch']]
        run([*command, '--', entry['repository'], prepared])
        if read_json(prepared / 'composer.json').get('name') != entry['composer_name']:
            raise ValueError('Nome Composer remoto diferente do catalogo.')
        os.replace(prepared, root)
    ws.scan()
    if not no_install:
        from uni_packages import Packages
        Packages(root, ws.catalog_file).install()
        installed = read_json(root / 'vendor/composer/installed.json')
        entries = installed if isinstance(installed, list) else installed.get('packages', [])
        if any(p['name'] == 'uniube/application-core' for p in entries):
            from uni import build_routes
            build_routes(root, update_dependencies=False)
    print('Projeto clonado: ' + str(root))


def push_project(root, message=None):
    root = git_root(root)
    run(['git', 'remote', 'get-url', 'origin'], root)
    branch = run(['git', 'branch', '--show-current'], root)
    if not branch: raise ValueError('HEAD destacado; selecione uma branch antes de publicar.')
    if run(['git', 'diff', '--name-only', '--diff-filter=U'], root):
        raise ValueError('Resolva os conflitos antes de publicar.')
    for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'rebase-merge', 'rebase-apply'):
        marker_path = Path(run(['git', 'rev-parse', '--git-path', marker], root))
        if not marker_path.is_absolute(): marker_path = root / marker_path
        if marker_path.exists(): raise ValueError('Conclua a operacao Git em andamento antes de publicar.')
    changes = run(['git', 'status', '--short'], root)
    if changes:
        print(changes)
        if not message:
            if not sys_stdin_tty(): raise ValueError('Alteracoes sem commit: informe --message para revisar/publicar conscientemente.')
            message = input('Mensagem do commit (vazio cancela): ').strip()
        if not message: raise ValueError('Publicacao cancelada.')
        run(['git', 'add', '--all'], root)
        run(['git', 'commit', '-m', message], root)
    run(['git', 'push', '-u', 'origin', branch], root, capture=False)
