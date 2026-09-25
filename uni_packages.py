"""Pacotes Composer instalados em libs/, sem clones Git aninhados."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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

    def composer(self, arguments):
        from uni_runtime import run_tool
        result = run_tool(self.root, 'composer', arguments, capture=True)
        if result.stdout: print(result.stdout, file=sys.stderr, end='')
        if result.stderr: print(result.stderr, file=sys.stderr, end='')
        return result

    def managed(self, manifest):
        libraries = dict(manifest.get('extra', {}).get('uni', {}).get('libraries', {}))
        required = {**manifest.get('require', {}), **manifest.get('require-dev', {})}
        for alias, entry in self.catalog.items():
            if entry['composer_name'] in required:
                libraries.setdefault(alias, entry)
        return libraries

    def preflight(self, libraries):
        # Installed packages are not development clones. Never remove their Git automatically.
        for entry in libraries.values():
            path = self.path(entry)
            if (path / '.git').exists():
                raise ValueError('Clone Git em libs/: ' + str(path) +
                                 '. Preserve as alteracoes em um clone separado antes da migracao.')
        if (self.root / '.git').exists() and libraries:
            paths = [str(self.path(e).relative_to(self.root)) for e in libraries.values()]
            result = subprocess.run(['git', '-c', 'core.filemode=false', '-c', 'core.autocrlf=true',
                                     'status', '--porcelain', '--untracked-files=all', '--', *paths],
                                    cwd=self.root, capture_output=True, text=True)
            if result.returncode:
                raise ValueError('Nao foi possivel verificar alteracoes locais em libs/: ' + result.stderr.strip())
            changed = [line for line in result.stdout.splitlines()
                       if '.compiled.' not in line and not line.endswith('/.gitignore')]
            if changed:
                raise ValueError('Fontes modificadas em libs/. Publique-as no repositorio da biblioteca '
                                 'e registre o estado no projeto antes do update, ou use build --skip-update.\n'
                                 + '\n'.join(changed[:10]))

    def configure(self, manifest, libraries):
        libraries = {alias: {k: v for k, v in entry.items() if k != 'ref'}
                     for alias, entry in libraries.items()}
        for entry in libraries.values():
            self.path(entry)
            self.validate_name(entry['composer_name'])
        extra = manifest.setdefault('extra', {})
        extra.setdefault('uni', {})['libraries'] = libraries
        repositories = manifest.setdefault('repositories', [])
        if not isinstance(repositories, list):
            raise ValueError('repositories deve ser uma lista no composer.json.')
        repositories[:] = [r for r in repositories
                           if not (r.get('type') == 'path' and r.get('url', '').rstrip('/') in ('libs/*', './libs/*'))]
        config = manifest.setdefault('config', {})
        config['preferred-install'] = 'dist'
        config['source-fallback'] = False
        if libraries:
            manifest.setdefault('minimum-stability', 'dev')
            manifest.setdefault('prefer-stable', True)
            manifest.setdefault('require', {})['oomphinc/composer-installers-extender'] = '^2.0'
            plugins = config.setdefault('allow-plugins', {})
            if not isinstance(plugins, dict):
                raise ValueError('allow-plugins precisa ser um objeto de permissoes explicitas.')
            plugins.update({'composer/installers': True, 'oomphinc/composer-installers-extender': True})
            types = extra.setdefault('installer-types', [])
            if 'library' not in types: types.append('library')
        paths = extra.setdefault('installer-paths', {})
        namespaces = {'application': 'ApplicationCore', 'frontend': 'FrontendCore',
                      'components': 'ComponentsCore', 'backend': 'BackendCore'}
        autoload = manifest.setdefault('autoload', {})
        psr = autoload.setdefault('psr-4', {})
        files = autoload.setdefault('files', [])
        # Internal manifests own their bootstrap files. Root-level duplicates
        # would execute the same registration twice after a real install.
        bootstraps = {'libs/' + e['root_name'] + '/register.php' for e in libraries.values()}
        files[:] = [f for f in files if f not in bootstraps]
        excluded = autoload.setdefault('exclude-from-classmap', [])
        if '**/*.compiled.php' not in excluded: excluded.append('**/*.compiled.php')
        for alias, known in self.catalog.items():
            directory = 'libs/' + known['root_name'] + '/'
            namespace = known.get('namespace', namespaces.get(alias))
            if alias not in libraries:
                if namespace and psr.get(namespace + chr(92)) == directory:
                    psr.pop(namespace + chr(92))
                files[:] = [f for f in files if f != directory + 'register.php']
                paths.pop(directory, None)
        for alias, entry in libraries.items():
            if entry.get('provider') == 'gogs':
                # Metadata is refreshed before update; install consumes the existing lock.
                repositories[:] = [r for r in repositories if r.get('url') != entry['repository']]
                directory = 'libs/' + entry['root_name'] + '/'
                paths[directory] = [entry['composer_name']]
                namespace = entry.get('namespace', namespaces.get(alias))
                if namespace: psr[namespace + chr(92)] = directory
                continue
            repository = next((r for r in repositories if r.get('url') == entry['repository']), None)
            if repository is None:
                repositories.append({'type': 'vcs', 'url': entry['repository']})
            else:
                repository['type'] = 'vcs'
            directory = 'libs/' + entry['root_name'] + '/'
            paths[directory] = [entry['composer_name']]
            namespace = entry.get('namespace', namespaces.get(alias))
            if namespace: psr[namespace + chr(92)] = directory
        # The extender handles every package of type library. It also needs a
        # fallback for external libraries, after the explicit internal paths.
        if libraries:
            fallback = config.get('vendor-dir', 'vendor').rstrip('/') + '/{$vendor}/{$name}/'
            paths.pop(fallback, None)
            paths[fallback] = ['type:library']
        return manifest

    def configure_project(self):
        manifest = read_json(self.manifest_file)
        libraries = self.managed(manifest)
        # Legacy metadata used to be the only declaration of the libraries.
        for entry in libraries.values():
            manifest.setdefault('require', {}).setdefault(entry['composer_name'], 'dev-' + entry.get('branch', 'main'))
        self.configure(manifest, libraries)
        write_json(self.manifest_file, manifest)
        from uni_distribution import prepare_distribution
        prepare_distribution(self.root)
        return libraries

    def install(self):
        with self.locked():
            manifest = read_json(self.manifest_file)
            libraries = self.managed(manifest)
            self.preflight(libraries)
            configured = self.configure(json.loads(json.dumps(manifest)), libraries)
            if configured != manifest:
                raise ValueError('Configuracao antiga. Execute uni composer configure e uni composer update antes do install.')
            self.prepare_locked_archives(libraries)
            self.composer(['install', '--prefer-dist', '--no-scripts', '--no-interaction'])
            self.finish()

    def prepare_locked_archives(self, libraries):
        lock = self.root / 'composer.lock'
        gogs = [e for e in libraries.values() if e.get('provider') == 'gogs']
        if not gogs: return
        if not lock.is_file(): raise ValueError('Sem lock. Execute uni composer update primeiro.')
        locked = {p['name']: p for p in read_json(lock).get('packages', [])}
        from uni_auth import composer_environment
        from uni_gogs import package
        old_auth = os.environ.get('COMPOSER_AUTH')
        auth = composer_environment(self.root).get('COMPOSER_AUTH')
        try:
            if auth: os.environ['COMPOSER_AUTH'] = auth
            for entry in gogs:
                item = locked.get(entry['composer_name'])
                if not item: raise ValueError('Biblioteca ausente do lock. Execute uni composer update.')
                if item.get('source', {}).get('url') != entry['repository']:
                    raise ValueError('Repositorio diferente do lock. Execute uni composer update.')
                url = item.get('dist', {}).get('url', '')
                if not url.startswith('.uni/packages/') or not (self.root / url).resolve().is_relative_to(self.root):
                    raise ValueError('Archive Gogs invalido no lock.')
                if not (self.root / url).is_file():
                    result = package(entry, self.root, reference=item['source']['reference'])
                    if result['package']['dist']['url'] != url:
                        raise ValueError('Archive reconstruido diferente do lock.')
        finally:
            if old_auth is None: os.environ.pop('COMPOSER_AUTH', None)
            else: os.environ['COMPOSER_AUTH'] = old_auth

    def finish(self):
        from uni_distribution import prepare_distribution
        prepare_distribution(self.root)
        for entry in self.managed(read_json(self.manifest_file)).values():
            if (self.path(entry) / '.git').exists():
                raise ValueError('Composer instalou fontes com Git. Use dist; metadados preservados para revisao.')

    def change(self, action, names, version=None, no_install=False):
        with self.locked():
            if action not in ('add', 'update', 'remove'):
                raise ValueError('Operacao Composer desconhecida.')
            manifest = read_json(self.manifest_file)
            libraries = self.managed(manifest)
            packages = [self.catalog[self.alias(n)]['composer_name'] if self.alias(n) else n for n in names]
            for package in packages: self.validate_name(package)
            if version and any(self.alias(n) for n in names):
                raise ValueError('--version e para pacotes externos.')
            if action == 'add':
                for alias in self.closure([n for n in names if self.alias(n)]):
                    entry = libraries.setdefault(alias, self.catalog[alias])
                    manifest.setdefault('require', {}).setdefault(entry['composer_name'], 'dev-' + entry.get('branch', 'main'))
            if action == 'update':
                for n in names:
                    if self.alias(n) and self.alias(n) not in libraries:
                        raise ValueError('Biblioteca nao gerenciada; use composer add: ' + n)
                for entry in libraries.values():
                    manifest.setdefault('require', {}).setdefault(entry['composer_name'], 'dev-' + entry.get('branch', 'main'))
                if not packages:
                    packages = [e['composer_name'] for e in libraries.values()]
                    if not packages:
                        raise ValueError('Informe os pacotes externos que deseja atualizar.')
            if action == 'remove':
                required = manifest.get('require', {})
                for package in packages:
                    if package not in required:
                        raise ValueError('Pacote nao e dependencia direta: ' + package)
                for alias, entry in libraries.items():
                    if entry['composer_name'] in packages: continue
                    for dependency in self.closure(entry.get('dependencies', [])):
                        if self.catalog[dependency]['composer_name'] in packages:
                            raise ValueError('Remova tambem a biblioteca dependente: ' + alias)
                for package in packages: required.pop(package)
                libraries = {a: e for a, e in libraries.items() if e['composer_name'] not in packages}
            self.preflight({**self.managed(read_json(self.manifest_file)), **libraries})
            before = self.manifest_file.read_bytes()
            lock = self.root / 'composer.lock'
            lock_before = lock.read_bytes() if lock.exists() else None
            self.configure(manifest, libraries)
            try:
                if not no_install:
                    from uni_gogs import package as gogs_package
                    from uni_auth import composer_environment
                    auth = composer_environment(self.root).get('COMPOSER_AUTH')
                    old_auth = os.environ.get('COMPOSER_AUTH')
                    try:
                        if auth: os.environ['COMPOSER_AUTH'] = auth
                        for entry in libraries.values():
                            if entry.get('provider') != 'gogs': continue
                            if action == 'update' and packages and entry['composer_name'] not in packages: continue
                            repository = gogs_package(entry, self.root)
                            manifest['repositories'] = [r for r in manifest['repositories']
                                if r.get('package', {}).get('name') != entry['composer_name']]
                            manifest['repositories'].insert(0, repository)
                    finally:
                        if old_auth is None: os.environ.pop('COMPOSER_AUTH', None)
                        else: os.environ['COMPOSER_AUTH'] = old_auth
                write_json(self.manifest_file, manifest)
                external = [n + (':' + version if version else '') for n in names if not self.alias(n)]
                if action == 'add' and external:
                    self.composer(['require', *external, '--no-update', '--no-scripts', '--no-interaction'])
                if no_install: return
                # Resolve first. On failure installed sources have not been changed.
                selected = packages if lock_before is not None else []
                if lock_before is not None:
                    locked_names = {p['name'] for p in json.loads(lock_before).get('packages', [])}
                    missing = [p for p in read_json(self.manifest_file).get('require', {})
                               if '/' in p and p not in locked_names]
                    selected = list(dict.fromkeys([*selected, *missing]))
                if action == 'add' and lock_before is not None:
                    selected = list(dict.fromkeys([*selected, *[e['composer_name'] for e in libraries.values()],
                                                   'oomphinc/composer-installers-extender', 'composer/installers']))
                self.composer(['update', *selected, '--with-dependencies', '--no-install',
                               '--prefer-dist', '--no-scripts', '--no-interaction'])
            except Exception:
                self.manifest_file.write_bytes(before)
                if lock_before is None: lock.unlink(missing_ok=True)
                else: lock.write_bytes(lock_before)
                raise
            # Keep the resolved lock if installation fails: rerun install to recover.
            self.prepare_locked_archives(libraries)
            self.composer(['install', '--prefer-dist', '--no-scripts', '--no-interaction'])
            self.finish()

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
        lock = self.root / 'composer.lock'
        locked = {p['name']: p for p in read_json(lock).get('packages', [])} if lock.exists() else {}
        print('Nucleo | Pacote | Instalado | Commit no lock')
        for alias, entry in self.catalog.items():
            package = installed.get(entry['composer_name'])
            print(' | '.join([alias, entry['composer_name'], package.get('version', '?') if package else 'nao', locked.get(entry['composer_name'], {}).get('source', {}).get('reference', '-')[:12]]))
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
