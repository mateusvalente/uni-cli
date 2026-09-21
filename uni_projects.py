"""Registro de projetos locais no catalogo compartilhavel."""
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from uni_packages import write_json


def repository_identity(repository):
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_.~/-]+', repository):
        host, path = repository[4:].split(':', 1)
    else:
        url = urlsplit(repository)
        if (url.scheme not in ('https', 'ssh') or not url.hostname or url.password
                or url.query or url.fragment or (url.scheme == 'https' and url.username)):
            raise ValueError('Informe uma URL Git HTTPS ou SSH sem senha ou token.')
        host, path = url.netloc, url.path
    path = path.strip('/').removesuffix('.git')
    if not path or any(part in ('', '.', '..') for part in path.split('/')):
        raise ValueError('Caminho de repositorio invalido.')
    return host.removeprefix('git@').lower() + '/' + path


def register_project(repository, root, catalog, name, load_config):
    identity = repository_identity(repository)
    name = name or identity.rsplit('/', 1)[-1]
    if not re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', name):
        raise ValueError('O nome do projeto deve usar letras minusculas, numeros, ponto, hifen ou sublinhado.')
    local_root = Path(root).resolve() if root else None
    if local_root:
        manifest = json.loads((local_root / 'composer.json').read_text(encoding='utf-8-sig'))
    else:
        manifest = remote_manifest(repository, Path(catalog).parent)
    root_name = local_root.name if local_root else name
    composer_name = manifest.get('name', '')
    if not re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*', composer_name):
        raise ValueError('composer.json precisa de um name valido no formato fornecedor/projeto.')
    config = load_config(catalog)
    for key, entry in config['projects'].items():
        same_repo = repository_identity(entry['repository']) == identity
        if key == name:
            if not same_repo or entry['composer_name'] != composer_name:
                raise ValueError(f"O nome '{name}' ja pertence a outro registro.")
            root_name = entry['root_name']
        elif same_repo or entry['composer_name'] == composer_name or entry['root_name'] == root_name:
            raise ValueError(f"Projeto ja registrado como '{key}'.")
    try:
        result = subprocess.run(['git', 'ls-remote', '--symref', '--', repository, 'HEAD'],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Tempo esgotado ao consultar o repositorio Git.') from exc
    if result.returncode:
        raise ValueError('Nao foi possivel acessar o repositorio Git. Confira o endereco e suas credenciais.')
    branch = re.search(r'^ref: refs/heads/(.+)\tHEAD$', result.stdout, re.MULTILINE)
    entry = dict(config['projects'].get(name, {}))
    entry.update(repository=repository, composer_name=composer_name, root_name=root_name)
    if branch:
        entry['branch'] = branch.group(1)
    config['projects'][name] = entry
    write_json(Path(catalog), config)
    return name


def remote_manifest(repository, directory):
    from uni_workspace import run
    scratch = Path(directory) / '.scratch'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='inspect-') as temporary:
        clone = Path(temporary) / 'repo'
        run(['git', 'clone', '--bare', '--depth', '1', '--', repository, clone])
        try:
            return json.loads(run(['git', 'show', 'HEAD:composer.json'], clone))
        except ValueError as exc:
            raise ValueError('O repositorio precisa de composer.json na branch padrao. Para Git vazio, use init ou --path.') from exc


def sync_catalog(catalog, deleting=None):
    """Consulta o catalogo remoto antes de validar duplicatas; nao perde registros locais."""
    from uni_workspace import git_root, run
    path = Path(catalog).resolve()
    root = git_root(path.parent)
    branch = run(['git', 'branch', '--show-current'], root)
    if not branch: raise ValueError('Catalogo em HEAD destacado.')
    run(['git', 'fetch', 'origin', branch], root)
    remote = json.loads(run(['git', 'show', f'origin/{branch}:{path.name}'], root))
    local = json.loads(path.read_text(encoding='utf-8-sig'))
    baseline = json.loads(run(['git', 'show', f'HEAD:{path.name}'], root))
    if deleting:
        target = remote['projects'].get(deleting)
        if target is not None and target != baseline['projects'].get(deleting):
            raise ValueError(f'Conflito no catalogo projects.{deleting}; nenhuma publicacao executada.')
        local['projects'].pop(deleting, None)
    for section in ('libs', 'projects'):
        for key, entry in remote[section].items():
            existing = local[section].get(key)
            old = baseline[section].get(key)
            if existing is None and old is not None:
                if entry != old:
                    raise ValueError(f'Conflito no catalogo {section}.{key}; nenhuma publicacao executada.')
                continue
            if existing is not None and existing != entry and existing != old and entry != old:
                raise ValueError(f'Conflito no catalogo {section}.{key}; nenhuma publicacao executada.')
            if existing is None or existing == old:
                local[section][key] = entry
        for key, old in baseline[section].items():
            if key not in remote[section]:
                existing = local[section].get(key)
                if existing == old:
                    del local[section][key]
                elif existing is not None:
                    raise ValueError(f'Conflito no catalogo {section}.{key}; nenhuma publicacao executada.')
    if deleting:
        dependents = [name for name, entry in local['projects'].items()
                      if entry.get('related', {}).get('backend') == deleting]
        if dependents:
            raise ValueError('Projeto associado como backend de: ' + ', '.join(dependents))
    write_json(path, local)


def publish_catalog(catalog):
    from uni_workspace import git_root, run
    path = Path(catalog).resolve()
    root = git_root(path.parent)
    branch = run(['git', 'branch', '--show-current'], root)
    if not branch: raise ValueError('Selecione a branch do uni-cli antes de publicar.')
    run(['git', 'fetch', 'origin', branch], root)
    behind, _ = map(int, run(['git', 'rev-list', '--left-right', '--count', f'origin/{branch}...HEAD'], root).split())
    if behind:
        raise ValueError('Cadastro salvo localmente. Sincronize a branch do uni-cli antes de publicar; nenhum push forcado sera feito.')
    if run(['git', 'diff', '--name-only', '--diff-filter=U'], root):
        raise ValueError('Resolva os conflitos do uni-cli antes de publicar o catalogo.')
    if run(['git', 'diff', 'HEAD', '--', path.name], root):
        run(['git', 'add', '--', path.name], root)
        run(['git', 'commit', '--only', '-m', 'Update project catalog', '--', path.name], root)
    try:
        run(['git', 'push', 'origin', f'HEAD:{branch}'], root, capture=False)
    except ValueError as exc:
        raise ValueError('Cadastro commitado localmente, mas push pendente. Use uni push --cli para retomar.') from exc
    url = json.loads(path.read_text(encoding='utf-8-sig')).get('catalog_url')
    if url:
        from urllib.request import urlopen
        expected = json.loads(path.read_text(encoding='utf-8-sig'))
        try:
            with urlopen(url + ('&' if '?' in url else '?') + 'revision=' + run(['git', 'rev-parse', 'HEAD'], root), timeout=15) as response:
                published = json.load(response)
            if published != expected:
                print('Push concluido; GitHub Pages ainda esta propagando o JSON. Confira com uni catalog verify.')
            else: print('Catalogo publicado e verificado pela URL.')
        except (OSError, ValueError):
            print('Push concluido; verificacao HTTP pendente. Use uni catalog verify.')


def configure_registration(name, catalog, role=None, environment=None, backend=None, docker_repository=None):
    from uni_workspace import slug
    path = Path(catalog)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    entry = data['projects'][name]
    role = role or entry.get('role', 'back' if name.endswith('-back') else 'front')
    environment = slug(environment or entry.get('environment', re.sub(r'-(front|back)$', '', name)))
    entry.update(role=role, environment=environment)
    if backend:
        if backend not in data['projects'] or backend == name:
            raise ValueError('Backend associado nao registrado: ' + backend)
        entry.setdefault('related', {})['backend'] = backend
    if docker_repository:
        repository_identity(docker_repository)
        entry['docker'] = {'repository': docker_repository, 'branch': name, 'directory': 'docker-' + role}
    write_json(path, data)
    return entry


def register_and_publish(args, load_config):
    from uni_workspace import Workspace, run
    if not args.local:
        sync_catalog(args.catalog)
    snapshot = Path(args.catalog).read_bytes()
    try:
        name = register_project(args.repository, args.path, args.catalog, args.name, load_config)
        entry = configure_registration(name, args.catalog, args.role, args.environment, args.backend, args.docker_repository)
    except Exception:
        Path(args.catalog).write_bytes(snapshot)
        raise
    if not args.local and not entry.get('docker'):
        docker_repository = load_config(args.catalog).get('docker_repository')
        if docker_repository:
            ws = Workspace(catalog=args.catalog)
            if not run(['git', 'ls-remote', docker_repository, 'refs/heads/' + name]):
                from uni_init import docker_branch
                docker_branch(ws, name, entry['role'], entry['environment'], docker_repository,
                              8082 if entry['role'] == 'back' else 8080, args.backend)
            configure_registration(name, args.catalog, docker_repository=docker_repository)
    try:
        ws = Workspace(catalog=args.catalog)
        ws.scan()
    except ValueError as exc:
        print('Cadastro salvo; mapa local nao atualizado: ' + str(exc))
    if not args.local:
        publish_catalog(args.catalog)
    else:
        print('Registro local; publicacao pendente (uni catalog publish).')
    return name


def delete_and_publish(args, load_config):
    data = load_config(args.catalog)
    if args.name not in data['projects']:
        raise ValueError('Projeto nao cadastrado: ' + args.name)
    entry = data['projects'][args.name]
    dependents = [name for name, entry in data['projects'].items()
                  if entry.get('related', {}).get('backend') == args.name]
    if dependents:
        raise ValueError('Projeto associado como backend de: ' + ', '.join(dependents))
    if not args.local:
        sync_catalog(args.catalog, deleting=args.name)
    else:
        del data['projects'][args.name]
        write_json(Path(args.catalog), data)
    docker = entry.get('docker') or {}
    if docker.get('branch'):
        print(f"Aviso: pastas locais e a branch Docker '{docker['branch']}' em {docker.get('repository', 'seu repositorio Docker')} nao foram apagadas. "
              'Depois da limpeza local, solicite a exclusao dessa branch ou apague-a manualmente.')
    if not args.local:
        publish_catalog(args.catalog)
    else:
        print('Exclusao local; publicacao pendente (uni catalog publish).')
