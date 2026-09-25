"""Registro de projetos locais no catalogo compartilhavel."""
import json
import os
import re
import subprocess
import sys
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


def choose_link(args, role):
    """Uma escolha explícita por projeto; --backend é a forma antiga de --link."""
    backend = getattr(args, 'backend', None)
    if backend and role != 'front':
        raise ValueError('Somente frontend pode usar --backend.')
    if backend and getattr(args, 'no_link', False):
        raise ValueError('--backend e --no-link nao podem ser usados juntos.')
    if backend or getattr(args, 'link', False):
        return True
    if getattr(args, 'no_link', False):
        return False
    if not sys.stdin.isatty():
        raise ValueError('Sem terminal interativo: informe --link ou --no-link.')
    while True:
        answer = input('Deseja vincular frontend e backend neste ambiente? [s/n]: ').strip().lower()
        if answer in ('s', 'sim'): return True
        if answer in ('n', 'nao', 'não'): return False


def wants_link(entry):
    related = entry.get('related', {})
    return related.get('link_requested', bool(related.get('backend')))


def configure_registration(name, catalog, role=None, environment=None, backend=None, docker_repository=None,
                           link_requested=None):
    from uni_workspace import slug
    path = Path(catalog)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    entry = data['projects'][name]
    role = role or entry.get('role', 'back' if name.endswith('-back') else 'front')
    environment = slug(environment or entry.get('environment', re.sub(r'-(front|back)$', '', name)))
    entry.update(role=role, environment=environment)
    if backend:
        other = data['projects'].get(backend)
        if not other or backend == name:
            raise ValueError('Backend associado nao registrado: ' + backend)
        if role != 'front' or other.get('role', 'back' if backend.endswith('-back') else 'front') != 'back':
            raise ValueError('O projeto associado precisa ser um backend.')
        if other.get('environment') != environment:
            raise ValueError('Frontend e backend precisam estar no mesmo ambiente.')
    if link_requested is not None:
        entry.setdefault('related', {})['link_requested'] = bool(link_requested)
    if docker_repository:
        repository_identity(docker_repository)
        entry['docker'] = {'repository': docker_repository, 'branch': name, 'directory': 'docker-' + role}
    write_json(path, data)
    return entry


def update_front_docker(front, back, entry):
    """Atualiza somente os dois arquivos gerados; mudanças manuais exigem revisão humana."""
    from uni_init import docker_files
    from uni_workspace import run
    docker = entry['docker']
    repository_identity(docker['repository'])
    branch = docker['branch']
    with tempfile.TemporaryDirectory(prefix='uni-link-') as temporary:
        root = Path(temporary) / 'front'
        run(['git', 'clone', '--branch', branch, '--single-branch', '--', docker['repository'], root])
        example = (root / '.env.example').read_text(encoding='utf-8')
        match = re.search(r'^APP_PORT=(\d+)$', example, re.MULTILINE)
        if not match:
            raise ValueError('Docker frontend sem APP_PORT gerada; vinculo manual necessario.')
        port = int(match.group(1))
        current = ((root / 'compose.yaml').read_text(encoding='utf-8'),
                   (root / 'nginx/default.conf').read_text(encoding='utf-8'))
        desired = docker_files(front, 'front', entry['environment'], port, back)
        if current != desired:
            previous = entry.get('related', {}).get('backend')
            expected = docker_files(front, 'front', entry['environment'], port, previous)
            unlinked = docker_files(front, 'front', entry['environment'], port)
            if current not in (expected, unlinked):
                raise ValueError('Compose/Nginx do frontend foram personalizados; revise o vinculo manualmente.')
            (root / 'compose.yaml').write_text(desired[0], encoding='utf-8')
            (root / 'nginx/default.conf').write_text(desired[1], encoding='utf-8')
            run(['git', 'add', '--', 'compose.yaml', 'nginx/default.conf'], root)
            run(['git', 'diff', '--cached', '--check'], root)
            run(['git', 'commit', '-m', f'Link {front} to {back}'], root)
            try:
                run(['git', 'push', 'origin', 'HEAD:' + branch], root, capture=False)
            except ValueError as exc:
                raise ValueError(f'Push Docker recusado. Resolva a divergencia e repita: uni project link {front} {back}') from exc


def link_projects(front, back, catalog, load_config):
    """Vinculo explícito, seguro para repetir após uma falha de publicação."""
    from uni_workspace import slug
    slug(front); slug(back)
    sync_catalog(catalog)
    data = load_config(catalog)
    front_entry = data['projects'].get(front)
    back_entry = data['projects'].get(back)
    if not front_entry or not back_entry:
        raise ValueError('Cadastre frontend e backend antes de vincular.')
    if front_entry.get('role') != 'front' or back_entry.get('role') != 'back':
        raise ValueError('Informe FRONT BACK com os papeis corretos.')
    if front_entry.get('environment') != back_entry.get('environment'):
        raise ValueError('Frontend e backend precisam estar no mesmo ambiente.')
    if not front_entry.get('docker') or not back_entry.get('docker'):
        raise ValueError('Publique as duas branches Docker antes de executar uni project link.')
    update_front_docker(front, back, front_entry)
    if (front_entry.get('related', {}).get('backend') == back
            and wants_link(front_entry) and wants_link(back_entry)):
        return
    snapshot = Path(catalog).read_bytes()
    data['projects'][front].setdefault('related', {}).update(link_requested=True, backend=back)
    data['projects'][back].setdefault('related', {})['link_requested'] = True
    write_json(Path(catalog), data)
    try:
        publish_catalog(catalog)
    except ValueError as exc:
        Path(catalog).write_bytes(snapshot)
        raise ValueError(f'Vinculo nao publicado; catalogo local restaurado. Se houve commit local, '
                         f'revise-o antes de uni push --cli. Repita: uni project link {front} {back}') from exc


def matching_project(name, role, environment, catalog, preferred=None):
    projects = catalog['projects']
    opposite = 'back' if role == 'front' else 'front'
    matches = [key for key, item in projects.items()
               if key != name and item.get('role') == opposite and item.get('environment') == environment]
    if preferred:
        if preferred not in matches:
            raise ValueError('Backend informado precisa estar cadastrado no mesmo ambiente.')
        return preferred
    if len(matches) > 1:
        raise ValueError('Mais de um projeto compativel no ambiente; informe --backend ou use uni project link.')
    return matches[0] if matches else None


def planned_backend(name, role, environment, catalog, preferred=None):
    if role != 'front' or not wants_link(catalog['projects'][name]):
        return None
    back = matching_project(name, role, environment, catalog, preferred)
    candidate = catalog['projects'].get(back, {})
    if back and (wants_link(candidate) or preferred) and candidate.get('docker'):
        return back
    return None


def auto_link(name, catalog, load_config, preferred=None):
    data = load_config(catalog)
    entry = data['projects'][name]
    other = matching_project(name, entry['role'], entry['environment'], data, preferred)
    if not other or not wants_link(entry):
        return
    counterpart = data['projects'][other]
    if not wants_link(counterpart) and not preferred:
        if entry['role'] != 'back' or not sys.stdin.isatty():
            print(f'{other} escolheu nao vincular. Para alterar essa decisao: uni project link '
                  + (other if entry['role'] == 'back' else name) + ' '
                  + (name if entry['role'] == 'back' else other))
            return
        response = input(f'{other} escolheu nao vincular. Confirma alterar a decisao e vincular? [s/N]: ').strip().lower()
        if response not in ('s', 'sim'):
            return
    front, back = (name, other) if entry['role'] == 'front' else (other, name)
    try:
        link_projects(front, back, catalog, load_config)
    except ValueError as exc:
        print(f'Projetos cadastrados sem vinculo: {exc} Repita: uni project link {front} {back}')


def register_and_publish(args, load_config):
    from uni_workspace import Workspace, run
    if not args.local:
        sync_catalog(args.catalog)
    snapshot = Path(args.catalog).read_bytes()
    try:
        name = register_project(args.repository, args.path, args.catalog, args.name, load_config)
        role = args.role or load_config(args.catalog)['projects'][name].get('role') or ('back' if name.endswith('-back') else 'front')
        requested = choose_link(args, role)
        entry = configure_registration(name, args.catalog, role, args.environment, args.backend,
                                       link_requested=requested)
    except Exception:
        Path(args.catalog).write_bytes(snapshot)
        raise
    if not args.local and not entry.get('docker'):
        docker_repository = args.docker_repository or load_config(args.catalog).get('docker_repository')
        if docker_repository:
            ws = Workspace(catalog=args.catalog)
            if not run(['git', 'ls-remote', docker_repository, 'refs/heads/' + name]):
                from uni_init import docker_branch
                backend = planned_backend(name, entry['role'], entry['environment'], load_config(args.catalog), args.backend)
                docker_branch(ws, name, entry['role'], entry['environment'], docker_repository,
                              8082 if entry['role'] == 'back' else 8080, backend)
            configure_registration(name, args.catalog, docker_repository=docker_repository)
    try:
        ws = Workspace(catalog=args.catalog)
        ws.scan()
    except ValueError as exc:
        print('Cadastro salvo; mapa local nao atualizado: ' + str(exc))
    if not args.local:
        publish_catalog(args.catalog)
        auto_link(name, args.catalog, load_config, args.backend)
    else:
        print('Registro local; publicacao pendente (uni catalog publish).')
    if args.path:
        manifest_path = Path(args.path).resolve() / 'composer.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        manifest.setdefault('extra', {}).setdefault('uni', {}).setdefault('related', {})['link_requested'] = requested
        write_json(manifest_path, manifest)
    return name


def delete_and_publish(args, load_config):
    from uni_workspace import git_root, run, slug
    data = load_config(args.catalog)
    slug(args.name)
    entry = data['projects'].get(args.name)
    if not entry and not args.local:
        sync_catalog(args.catalog)
        root = git_root(Path(args.catalog).parent)
        branch = run(['git', 'branch', '--show-current'], root)
        remote = json.loads(run(['git', 'show', f'origin/{branch}:{Path(args.catalog).name}'], root))
        entry = remote['projects'].get(args.name)
    if entry:
        dependents = [name for name, item in {**data['projects'], args.name: entry}.items()
                      if item.get('related', {}).get('backend') == args.name]
        if dependents:
            raise ValueError('Projeto associado como backend de: ' + ', '.join(dependents))
    docker = (entry or {}).get('docker') or {}
    repository = docker.get('repository') or (getattr(args, 'docker_repository', None) if not entry else data.get('docker_repository'))
    branch = docker.get('branch', args.name)
    if repository:
        repository_identity(repository)
        slug(branch)
        if branch == 'main':
            raise ValueError('A branch Docker main nao pode ser excluida: ' + repository)
    elif not entry:
        raise ValueError('Projeto nao cadastrado; informe --docker-repository para excluir a branch orfa: ' + args.name)

    if entry:
        if not args.local:
            sync_catalog(args.catalog, deleting=args.name)
        else:
            del data['projects'][args.name]
            write_json(Path(args.catalog), data)
    elif args.local:
        raise ValueError('Projeto nao cadastrado: ' + args.name)

    if args.local:
        print('Exclusao local; branch Docker preservada em ' + str(repository or 'repositorio nao configurado') + '. Publicacao pendente (uni catalog publish).')
        return

    if entry:
        publish_catalog(args.catalog)
    if not repository:
        print('Cadastro removido; nenhuma branch Docker configurada.')
        return
    if not run(['git', 'ls-remote', repository, 'refs/heads/' + branch]):
        if entry:
            print(f"Cadastro removido; branch Docker '{branch}' ja nao existe em {repository}.")
            return
        raise ValueError(f"Projeto nao cadastrado e branch Docker '{branch}' nao existe em {repository}.")
    try:
        run(['git', 'push', repository, '--delete', branch], capture=False)
    except ValueError as exc:
        raise ValueError(f"Cadastro removido; branch Docker '{branch}' continua em {repository}. "
                         f"Retome com: uni project delete {args.name} --docker-repository {repository}") from exc
    print(f"Branch Docker '{branch}' excluida em {repository}.")
