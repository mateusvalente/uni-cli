"""Criacao no host e publicacao dos repositorios de projeto/Docker/catalogo."""
import json
import re
import tempfile
from pathlib import Path

from uni_packages import Packages, read_json, write_json
from uni_projects import (repository_identity, register_project, configure_registration,
                          sync_catalog, publish_catalog, choose_link, matching_project,
                          planned_backend, auto_link)
from uni_workspace import Workspace, doctor, run, slug


def docker_files(name, role, environment, port, backend=None):
    compose = f'''name: ${{COMPOSE_PROJECT_NAME:-{name}}}
services:
  php:
    image: php:8.5-fpm-alpine
    working_dir: /var/www/html
    command: ["sh", "/usr/local/bin/start-framework.sh"]
    volumes:
      - ${{PROJECT_PATH:?Configure PROJECT_PATH}}:/var/www/html
      - ./php/start.sh:/usr/local/bin/start-framework.sh:ro
      - ./php/development.ini:/usr/local/etc/php/conf.d/zz-development.ini:ro
    restart: unless-stopped
  nginx:
    image: nginx:stable-alpine
    ports: ["127.0.0.1:${{APP_PORT:-{port}}}:80"]
    volumes:
      - ${{PROJECT_PATH:?Configure PROJECT_PATH}}/public:/var/www/html/public:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [php]
    restart: unless-stopped
    networks:
      default:
      environment:
        aliases: [{name}]
networks:
  environment:
    name: ${{ENVIRONMENT_NETWORK:-{environment}-network}}
'''
    if backend: compose += '    external: true\n'
    proxy = f'''    resolver 127.0.0.11 valid=10s ipv6=off;
    location ~ ^/api(?:/|$) {{
        set $backend http://{backend}:80;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass $backend$request_uri;
    }}
''' if backend else ''
    nginx = '''server {
    listen 80;
    root /var/www/html/public;
    index index.php;
    charset utf-8;
    client_max_body_size 20m;
''' + proxy + '''    location / { try_files $uri $uri/ /index.php?$query_string; }
    location = /index.php {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass php:9000;
    }
    location ~ \\.php(?:/|$) { return 404; }
    location ~ /\\. { deny all; }
}
'''
    return compose, nginx


def docker_branch(ws, name, role, environment, repository, port, backend=None, publish=True):
    """Deriva sempre da main; nunca altera a branch modelo nem ambientes ativos."""
    repository_identity(repository)
    slug(name); slug(environment)
    scratch = ws.root / '.uni/tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='docker-') as temporary:
        root = Path(temporary) / 'source'
        run(['git', 'clone', '--branch', 'main', '--single-branch', '--', repository, root])
        if run(['git', 'ls-remote', 'origin', 'refs/heads/' + name], root):
            raise ValueError('Branch Docker ja existe: ' + name)
        for required in ['uni/Dockerfile', 'php/start.sh', 'php/development.ini', 'nginx/default.conf']:
            if not (root / required).is_file():
                raise ValueError('Modelo Docker main incompleto: ' + required)
        run(['git', 'switch', '-c', name], root)
        compose, nginx = docker_files(name, role, environment, port, backend)
        (root / 'compose.yaml').write_text(compose, encoding='utf-8')
        (root / 'nginx/default.conf').write_text(nginx, encoding='utf-8')
        (root / '.gitattributes').write_text('* text=auto\n*.sh text eol=lf\n*.conf text eol=lf\n*.yaml text eol=lf\n', encoding='utf-8')
        (root / '.gitignore').write_text('.env\n.env.*\n!.env.example\n', encoding='utf-8')
        (root / '.env.example').write_text(f'PROJECT_PATH=../{name}\nCOMPOSE_PROJECT_NAME={name}\nAPP_PORT={port}\nENVIRONMENT_NETWORK={environment}-network\n', encoding='utf-8')
        script = root / 'php/start.sh'; script.write_bytes(script.read_bytes().replace(b'\r\n', b'\n'))
        (root / 'README.md').write_text(f'# {name}\n\nBranch do ambiente {environment}, papel {role}.\nUse uni use {environment} para configurar os caminhos locais e iniciar.\n', encoding='utf-8')
        run(['git', 'add', '--all'], root)
        run(['git', 'diff', '--cached', '--check'], root)
        run(['git', 'commit', '-m', 'Configure Docker for ' + name], root)
        if publish:
            run(['git', 'push', '-u', 'origin', name], root, capture=False)
        else:
            # O clone temporario nao pode ser a unica copia do commit preparado.
            bundle = ws.root / '.uni' / (name + '.docker.bundle')
            run(['git', 'bundle', 'create', bundle, name], root)


def initialize(args, init_project, select_libraries, load_config):
    doctor()
    ws = Workspace(catalog=args.catalog)
    if not args.local: sync_catalog(args.catalog)
    ws.catalog = load_config(args.catalog)
    ws.scan()
    package = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', args.project)
    package = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', package)
    package = re.sub(r'_+', '-', package).strip('-').lower()
    name = slug(args.name or package)
    if name in ws.catalog['projects'] or name in ws.state['projects']:
        raise ValueError('Nome de projeto ja cadastrado: ' + name)
    if any(e['composer_name'] == args.vendor + '/' + package for e in ws.catalog['projects'].values()):
        raise ValueError('Nome Composer ja cadastrado.')
    role = args.role or ('back' if name.endswith('-back') else 'front')
    environment = slug(args.environment or re.sub(r'-(front|back)$', '', name))
    requested = choose_link(args, role)
    if args.port is not None and not 1 <= args.port <= 65535:
        raise ValueError('Porta deve estar entre 1 e 65535.')
    if role in ws.state['environments'].get(environment, {}):
        raise ValueError('O ambiente ja possui um projeto ' + role)
    if args.backend and args.backend not in ws.catalog['projects']:
        raise ValueError('Backend nao cadastrado: ' + args.backend)
    if args.backend and role != 'front': raise ValueError('Somente frontend pode associar backend.')
    if args.backend:
        matching_project(name, role, environment, ws.catalog, args.backend)
    selected = select_libraries(args)
    root = Path(args.path).resolve() if args.path else ws.root / name
    if root.exists() and any(root.iterdir()):
        raise ValueError('A pasta de destino deve estar vazia: ' + str(root))
    repository = args.repository
    if not repository and not args.local:
        import sys
        if sys.stdin.isatty(): repository = input('Git do novo projeto (repositorio vazio): ').strip()
        else: raise ValueError('Informe --repository com o Git vazio do projeto.')
    if repository:
        repository_identity(repository)
        if run(['git', 'ls-remote', '--', repository]):
            raise ValueError('O Git informado ja possui referencias. Use project register/clone para projetos existentes.')
    docker = args.docker_repository or ws.catalog.get('docker_repository')
    if not docker and not args.local: raise ValueError('Configure docker_repository no catalogo ou informe --docker-repository.')
    if docker:
        repository_identity(docker)
        if not run(['git', 'ls-remote', docker, 'refs/heads/main']): raise ValueError('Docker precisa da branch modelo main.')
        if run(['git', 'ls-remote', docker, 'refs/heads/' + name]): raise ValueError('Branch Docker ja existe: ' + name)
    root.mkdir(parents=True, exist_ok=True)
    manager = Packages(root, args.catalog)
    closure = manager.closure(selected)
    if role == 'back' and any(x in closure for x in ('frontend', 'components')):
        raise ValueError('Backend nao pode depender de FrontendCore ou ComponentsCore.')
    init_project(args.vendor, args.project, root, framework='application' in closure)
    from uni_distribution import prepare_distribution
    prepare_distribution(root)
    manifest = read_json(root / 'composer.json')
    uni = manifest.setdefault('extra', {}).setdefault('uni', {})
    uni.update(role=role, environment=environment)
    uni['docker'] = {'port': args.port or (8082 if role == 'back' else 8080)}
    uni['related'] = {'link_requested': requested}
    write_json(root / 'composer.json', manifest)
    if selected: manager.change('add', selected, no_install=args.no_install)
    elif not args.no_install: manager.composer(['install', '--no-interaction'])
    if 'application' in closure and not args.no_install:
        from uni import build_routes
        build_routes(root, update_dependencies=False)
    run(['git', 'init', '-b', 'main'], root)
    if repository: run(['git', 'remote', 'add', 'origin', repository], root)
    run(['git', 'add', '--all'], root)
    run(['git', 'commit', '-m', 'Initialize ' + name], root)
    # Registra antes das publicacoes: se o push falhar, o trabalho fica recuperavel.
    if repository:
        register_project(repository, root, args.catalog, name, load_config)
        configure_registration(name, args.catalog, role, environment, args.backend, None, requested)
    ws.catalog = load_config(args.catalog); ws.scan()
    if not args.local:
        run(['git', 'push', '-u', 'origin', 'main'], root, capture=False)
        backend = planned_backend(name, role, environment, load_config(args.catalog), args.backend)
        docker_branch(ws, name, role, environment, docker, uni['docker']['port'], backend)
        configure_registration(name, args.catalog, role, environment, args.backend, docker, requested)
        publish_catalog(args.catalog)
        auto_link(name, args.catalog, load_config, args.backend)
        ws.catalog = load_config(args.catalog); ws.scan()
    print('Projeto criado no Windows: ' + str(root))
    if args.local: print('Modo local: repositorios e catalogo nao foram publicados.')
