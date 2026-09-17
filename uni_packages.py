"""Bibliotecas Git em libs/ e dependencias Composer, pelo mesmo CLI uni."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute(command, root):
    result = subprocess.run(command, cwd=root, text=True, encoding='utf-8', capture_output=True,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        raise ValueError(f"Falha em {command[0]}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


class Packages:
    def __init__(self, root, catalog):
        self.root = Path(root).resolve()
        self.catalog = read_json(catalog)['libs']
        self.manifest_file = self.root / 'composer.json'

    def alias(self, name):
        for alias, entry in self.catalog.items():
            if name in (alias, entry['composer_name'], entry['root_name'], entry['repository'].rsplit('/', 1)[-1].removesuffix('.git')):
                return alias
        return None

    def closure(self, names):
        result = []
        def visit(name, trail):
            alias = self.alias(name)
            if alias is None:
                raise ValueError('Biblioteca fora do catalogo: ' + name)
            if alias in trail:
                raise ValueError('Dependencia circular no catalogo: ' + alias)
            if alias in result:
                return
            for dependency in self.catalog[alias].get('dependencies', []):
                visit(dependency, [*trail, alias])
            result.append(alias)
        for name in names:
            visit(name, [])
        return result

    def path(self, entry):
        name = entry['root_name']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name):
            raise ValueError('Diretorio de biblioteca invalido: ' + name)
        path = self.root / 'libs' / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('Biblioteca deve permanecer dentro do projeto: ' + name)
        return path

    @contextmanager
    def locked(self):
        with (self.root / '.uni-composer.lock').open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise ValueError('Outra operacao de dependencias esta em andamento.') from error
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError('Outra operacao de dependencias esta em andamento.') from error
            yield

    def inspect(self, entry):
        path = self.path(entry)
        if not (path / '.git').exists():
            raise ValueError(f'{path}: fontes locais sem Git. Publique/adote o repositorio antes de gerenciar pelo CLI.')
        remote = execute(['git', 'remote', 'get-url', 'origin'], path)
        if remote != entry['repository']:
            raise ValueError('Remote diferente do registrado: ' + str(path))
        if execute(['git', 'status', '--porcelain'], path):
            raise ValueError('Biblioteca com alteracoes locais; commit ou stash antes de continuar: ' + str(path))
        return execute(['git', 'rev-parse', 'HEAD'], path)

    def sync(self, entry, update=False):
        path = self.path(entry)
        branch = entry.get('branch', 'main')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', branch) or '..' in branch:
            raise ValueError('Branch invalida.')
        old = self.inspect(entry) if path.exists() else None
        wanted = entry.get('ref')
        if update or not wanted:
            output = execute(['git', 'ls-remote', '--exit-code', entry['repository'], 'refs/heads/' + branch], self.root)
            wanted = output.split()[0]
        if not re.fullmatch(r'[a-f0-9]{40,64}', wanted):
            raise ValueError('Commit de biblioteca invalido.')
        if old is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Clone preparado separadamente; um erro nao deixa uma biblioteca pela metade.
            with tempfile.TemporaryDirectory(prefix='.uni-libs-', dir=self.root) as temporary:
                clone = Path(temporary) / 'source'
                execute(['git', 'clone', '--no-checkout', '--', entry['repository'], str(clone)], self.root)
                execute(['git', 'checkout', '--detach', wanted], clone)
                self.validate_package(clone, entry)
                os.replace(clone, path)
        elif old != wanted:
            execute(['git', 'fetch', 'origin', branch], path)
            if update:
                result = subprocess.run(['git', 'merge-base', '--is-ancestor', old, wanted], cwd=path, capture_output=True)
                if result.returncode:
                    raise ValueError('Atualizacao diverge dos commits locais: ' + str(path))
            execute(['git', 'checkout', '--detach', wanted], path)
        self.validate_package(path, entry)
        return {**entry, 'ref': wanted}

    def validate_package(self, path, entry):
        manifest = path / 'composer.json'
        if not manifest.is_file():
            raise ValueError('Repositorio ainda nao contem pacote Composer: ' + entry['repository'])
        if read_json(manifest).get('name') != entry['composer_name']:
            raise ValueError('Nome Composer nao corresponde ao catalogo: ' + entry['repository'])

    def composer(self, arguments):
        if shutil.which('composer'):
            command = ['composer', *arguments]
            environment = os.environ.copy()
        else:
            compose = Path(__file__).resolve().parent.parent / 'docker/compose.yaml'
            if not shutil.which('docker') or not compose.is_file():
                raise ValueError('Instale Composer ou disponibilize Docker e o ambiente docker/ do workspace.')
            manifest = read_json(self.manifest_file)
            environment = {**os.environ, 'PROJECT_PATH': str(self.root),
                           'COMPOSE_PROJECT_NAME': manifest['name'].split('/')[-1]}
            command = ['docker', 'compose', '-f', str(compose), 'run', '--rm', '--entrypoint', 'composer', 'uni', *arguments]
        result = subprocess.run(command, cwd=self.root, env=environment)
        if result.returncode:
            raise ValueError('Composer falhou; consulte a mensagem acima. As fontes locais foram preservadas.')

    def managed(self, manifest):
        return manifest.get('extra', {}).get('uni', {}).get('libraries', {})

    def configure(self, manifest, libraries):
        manifest.setdefault('extra', {}).setdefault('uni', {})['libraries'] = libraries
        if libraries:
            manifest['minimum-stability'] = 'dev'
            manifest['prefer-stable'] = True
        repositories = manifest.setdefault('repositories', [])
        if not isinstance(repositories, list):
            raise ValueError('repositories deve ser uma lista no composer.json.')
        path_repo = next((r for r in repositories if r.get('type') == 'path' and r.get('url') == 'libs/*'), None)
        if path_repo is None:
            path_repo = {'type': 'path', 'url': 'libs/*'}
            repositories.insert(0, path_repo)
        path_repo['options'] = {'symlink': True, 'reference': 'auto', 'versions': {
            entry['composer_name']: 'dev-' + entry.get('branch', 'main') for entry in libraries.values()}}
        return manifest

    def install(self):
        with self.locked():
            manifest = read_json(self.manifest_file)
            libraries = self.managed(manifest)
            lock = self.root / 'composer.lock'
            locked = {p['name']: p for p in read_json(lock).get('packages', [])} if lock.exists() else None
            for entry in libraries.values():
                if not entry.get('ref'):
                    raise ValueError('Biblioteca sem commit registrado; use composer add ou update.')
                if locked is not None and locked.get(entry['composer_name'], {}).get('dist', {}).get('reference') != entry['ref']:
                    raise ValueError('Commit diferente entre composer.json e composer.lock: ' + entry['composer_name'])
                path = self.path(entry)
                if path.exists() and self.inspect(entry) != entry['ref']:
                    raise ValueError('Commit local diferente do registrado; nao sera substituido: ' + str(path))
            for entry in libraries.values():
                self.sync(entry)
            self.composer(['install', '--no-interaction'])

    def change(self, action, names, version=None, no_install=False):
        with self.locked():
            manifest = read_json(self.manifest_file)
            before = self.manifest_file.read_bytes()
            lock = self.root / 'composer.lock'
            lock_before = lock.read_bytes() if lock.exists() else None
            libraries = dict(self.managed(manifest))
            previous = {}
            # Verificar todas as fontes antes de qualquer checkout ou Composer.
            for alias, entry in libraries.items():
                if self.path(entry).exists():
                    previous[alias] = self.inspect(entry)
            try:
                if action == 'add':
                    core = [self.alias(n) for n in names if self.alias(n)]
                    for alias in self.closure(core):
                        entry = libraries.get(alias, self.catalog[alias])
                        if self.path(entry).exists() and alias not in previous:
                            previous[alias] = self.inspect(entry)
                        libraries[alias] = self.sync(entry)
                    for name in names:
                        alias = self.alias(name)
                        package = self.catalog[alias]['composer_name'] if alias else name
                        self.validate_name(package)
                        if alias and version:
                            raise ValueError('Nucleos usam a branch registrada e commit fixado; --version e para pacotes externos.')
                        if alias:
                            manifest.setdefault('require', {})[package] = 'dev-' + libraries[alias].get('branch', 'main')
                    self.configure(manifest, libraries)
                    write_json(self.manifest_file, manifest)
                    external = [name + (':' + version if version else '') for name in names if not self.alias(name)]
                    if external:
                        self.composer(['require', *external, '--no-update', '--no-interaction'])
                    if not no_install:
                        selected = [self.catalog[self.alias(n)]['composer_name'] if self.alias(n) else n for n in names]
                        self.composer(['update', *(selected if lock_before is not None else []), '--with-all-dependencies', '--no-interaction'])
                elif action == 'update':
                    chosen = set(self.closure([n for n in names if self.alias(n)])) if names else set(libraries)
                    for alias in chosen:
                        if alias not in libraries:
                            raise ValueError('Biblioteca nao gerenciada; use composer add: ' + alias)
                        libraries[alias] = self.sync(libraries[alias], update=True)
                    self.configure(manifest, libraries)
                    write_json(self.manifest_file, manifest)
                    packages = [self.catalog[self.alias(n)]['composer_name'] if self.alias(n) else n for n in names]
                    for package in packages: self.validate_name(package)
                    self.composer(['update', *packages, '--with-all-dependencies', '--no-interaction'])
                elif action == 'remove':
                    packages = [self.catalog[self.alias(n)]['composer_name'] if self.alias(n) else n for n in names]
                    for package in packages:
                        self.validate_name(package)
                        if package not in manifest.get('require', {}):
                            raise ValueError('Pacote nao e dependencia direta do projeto: ' + package)
                    self.composer(['remove', *packages, '--with-all-dependencies', '--no-interaction'])
                    manifest = read_json(self.manifest_file)
                    installed = {p['name'] for p in read_json(lock).get('packages', [])} if lock.exists() else set()
                    retained = {a: e for a, e in libraries.items() if e['composer_name'] in installed}
                    self.configure(manifest, retained)
                    write_json(self.manifest_file, manifest)
                    self.composer(['update', '--lock', '--no-interaction'])
                    # Fontes desinstaladas ficam preservadas para nao apagar trabalho do usuario.
                    for alias in libraries.keys() - retained.keys():
                        print('Desinstalada; clone preservado em libs/: ' + alias)
                else:
                    raise ValueError('Operacao Composer desconhecida.')
            except Exception:
                self.manifest_file.write_bytes(before)
                if lock_before is None: lock.unlink(missing_ok=True)
                else: lock.write_bytes(lock_before)
                for alias, revision in previous.items():
                    entry = libraries.get(alias, self.catalog[alias])
                    path = self.path(entry)
                    if path.exists() and not execute(['git', 'status', '--porcelain'], path):
                        execute(['git', 'checkout', '--detach', revision], path)
                raise

    @staticmethod
    def validate_name(name):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*', name):
            raise ValueError('Nome de pacote invalido: ' + name)

    def listing(self):
        manifest = read_json(self.manifest_file) if self.manifest_file.exists() else {}
        installed_file = self.root / 'vendor/composer/installed.json'
        installed_data = read_json(installed_file) if installed_file.exists() else {}
        installed = {p['name']: p for p in (installed_data if isinstance(installed_data, list) else installed_data.get('packages', []))}
        libraries = self.managed(manifest)
        print('Nucleo | Pacote | Instalado | Commit registrado')
        for alias, entry in self.catalog.items():
            package = installed.get(entry['composer_name'])
            print(' | '.join([alias, entry['composer_name'], package.get('version', '?') if package else 'nao', libraries.get(alias, {}).get('ref', '-')[:12]]))
        known = {e['composer_name'] for e in self.catalog.values()}
        for name, entry in sorted(installed.items()):
            if name not in known: print(f"externa | {name} | {entry.get('version', '?')} | -")

    def show(self, name):
        alias = self.alias(name)
        if alias:
            entry = self.catalog[alias]
            manifest = read_json(self.manifest_file) if self.manifest_file.exists() else {}
            print(json.dumps({'alias': alias, **entry, 'project': self.managed(manifest).get(alias)}, indent=2, ensure_ascii=False))
        else:
            self.validate_name(name)
            self.composer(['show', name])
