#!/usr/bin/env python3
"""Auxiliar do framework: validacao do guia e inicializacao de projetos."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uni_packages import Packages
from uni_projects import register_project


DEFAULT_CONFIG = Path(__file__).resolve().with_name("libs_projects.json")
INDEX_CONTENT = """<?php

declare(strict_types=1);

use ApplicationCore\\Kernel\\HttpKernel;

require_once dirname(__DIR__) . '/vendor/autoload.php';

(new HttpKernel())->run();
"""


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Evita que chaves JSON repetidas ocultem uma configuracao anterior."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Chave duplicada no arquivo guia: {key}.")
        result[key] = value
    return result


def load_config(path: Path) -> dict[str, object]:
    """Le e valida o guia sem modificar arquivos ou executar o Composer."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(f"Arquivo guia nao encontrado: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Nao foi possivel ler o arquivo guia: {path}: {exc}") from exc

    try:
        config = json.loads(content, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON invalido em {path}, linha {exc.lineno}, coluna {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(config, dict):
        raise ValueError("O arquivo guia deve conter um objeto JSON.")

    for section in ("libs", "projects"):
        if section not in config or not isinstance(config[section], dict):
            raise ValueError(f"A secao '{section}' deve existir e ser um objeto JSON.")

        for name, entry in config[section].items():
            location = f"{section}.{name}"
            if not name.strip():
                raise ValueError(f"A secao '{section}' contem um nome vazio.")
            if not isinstance(entry, dict):
                raise ValueError(f"'{location}' deve ser um objeto JSON.")
            for field in ("repository", "composer_name", "root_name"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"'{location}.{field}' deve ser um texto nao vazio."
                    )

    return config


def init_project(vendor: str, project: str, root: Path, framework: bool = False) -> str:
    """Cria o manifesto e as pastas locais, preservando arquivos existentes."""
    if not re.fullmatch(r"[a-z0-9]+(?:[_.-][a-z0-9]+)*", vendor):
        raise ValueError("O prefixo Composer deve usar letras minusculas e numeros, separados por '.', '_' ou '-'.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project):
        raise ValueError("O nome do projeto deve comecar com uma letra e conter apenas letras ASCII, numeros ou '_'.")
    if project.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("O nome do projeto e reservado pelo Windows.")

    package = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", project)
    package = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", package)
    package = re.sub(r"_+", "-", package).strip("-").lower()
    composer_name = f"{vendor}/{package}"
    root = root.resolve()
    composer = root / "composer.json"
    for filename in ("composer.json", "composer.lock"):
        path = root / filename
        if path.exists() or path.is_symlink():
            raise ValueError(f"'{filename}' ja existe. O init nao sobrescreve um projeto existente.")

    directories = [root / "src"]
    directories.extend(root / "src" / name for name in ("Core", "Programs", "Config"))
    directories.append(root / "public")
    for directory in directories:
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ValueError(f"O destino deve permanecer dentro do projeto: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"O destino ja existe e nao e uma pasta: {directory}")

    index = root / "public" / "index.php"
    if index.is_symlink() or (index.exists() and not index.is_file()):
        raise ValueError("public/index.php deve ser um arquivo regular.")

    manifest = {
        "name": composer_name,
        "type": "project",
        "autoload": {"psr-4": {f"{project}\\": "src/"}, "exclude-from-classmap": ["**/*.compiled.php"]},
        "extra": {"uni": {"docker": {"port": 8080}}},
    }
    created = []
    created_index = False
    # Abertura exclusiva: uma segunda execucao nao pode truncar o manifesto.
    with composer.open("x", encoding="utf-8", newline="\n") as stream:
        try:
            for directory in directories:
                if not directory.exists():
                    directory.mkdir()
                    created.append(directory)
            if not index.exists():
                with index.open("x", encoding="utf-8", newline="\n") as index_stream:
                    created_index = True
                    index_stream.write(INDEX_CONTENT if framework else '<?php\ndeclare(strict_types=1);\nheader("Content-Type: text/html; charset=utf-8");\necho "<h1>Projeto inicializado</h1>";\n')
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        except OSError:
            stream.close()
            composer.unlink()
            if created_index:
                index.unlink()
            for directory in reversed(created):
                directory.rmdir()
            raise
    return composer_name


def select_libraries(args):
    if args.no_framework:
        return []
    if args.libs is not None:
        return args.libs
    if not sys.stdin.isatty():
        raise ValueError('Sem terminal interativo: informe --libs application frontend components backend ou --no-framework.')
    answer = input('Usar o framework? [s/N]: ').strip().lower()
    if answer not in ('s', 'sim', 'y', 'yes'):
        return []
    print('1 application | 2 frontend | 3 components | 4 backend (pacote inicial vazio)')
    choices = input('Quais partes? Separe por espaco ou virgula [1]: ').strip() or '1'
    aliases = {'1': 'application', '2': 'frontend', '3': 'components', '4': 'backend'}
    return [aliases.get(value, value) for value in re.split(r'[\s,]+', choices)]


def build_routes(root: Path) -> int:
    """O host apenas solicita o build completo ao worker Linux."""
    if Path('/.dockerenv').exists():
        from uni_build import build_routes as linux_build
        return linux_build(root)
    from uni_runtime import run_tool
    worker = Path(__file__).resolve().with_name('uni_build.py')
    result = run_tool(root.resolve(), 'python3', ['-B', str(worker), '/var/www/html'], capture=True)
    return json.loads(result.stdout)['routes']


def scaffold(kind: str, name: str, root: Path, project: str | None = None,
             page: str | None = None, layout: str = "AppLayout") -> list[Path]:
    """Gera fontes a partir dos templates, sem sobrescrever arquivos existentes."""
    root = root.resolve()
    kind = "component" if kind == "components" else kind
    reserved = {"class", "interface", "trait", "enum", "function", "new", "self", "parent", "static",
                "namespace", "use", "extends", "implements", "abstract", "final", "readonly", "match",
                "int", "string", "bool", "float", "array", "object", "mixed", "void", "never", "null",
                "true", "false", "return", "if", "else", "for", "foreach", "while", "switch", "case",
                "default", "try", "catch", "throw", "public", "private", "protected", "const", "echo",
                "list", "callable", "iterable", "yield", "fn", "empty", "isset", "unset", "include",
                "require", "clone", "global", "var", "break", "continue", "do", "finally", "as", "instanceof",
                "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

    def parts(value: str) -> list[str]:
        result = value.replace("\\", "/").split("/")
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part) or part.lower() in reserved for part in result):
            raise ValueError("Use nomes PHP validos separados por '/', sem caminhos absolutos, '..' ou palavras reservadas.")
        return result

    def suffixed(value: str, suffix: str) -> str:
        return value if value.endswith(suffix) else value + suffix

    names = parts(name)
    planned: dict[Path, str] = {}
    templates = Path(__file__).resolve().parent / ".dist/uni/templates"

    def template(filename: str, replacements: dict[str, str]) -> str:
        content = (templates / filename).read_text(encoding="utf-8")
        for key, value in replacements.items():
            content = content.replace("__" + key + "__", value)
        return content

    def bundle(directory: Path, namespace: str, classname: str, category: str) -> None:
        base = {"page": "FrontendCore\\Page\\Page", "layout": "FrontendCore\\Layout\\Layout",
                "component": "ComponentsCore\\BaseComponent"}[category]
        css = re.sub(r"(?<!^)([A-Z])", r"-\1", classname).lower().replace("_", "-")
        replacements = {"NAMESPACE": namespace, "CLASS": classname, "BASE_CLASS": base,
                        "BASE": base.split("\\")[-1], "CSS_CLASS": css,
                        "CONSTRUCTOR": ""}
        if category == "component":
            planned[directory / (classname + ".php")] = template("component.php.tpl", replacements)
            return
        extensions = [("html.php", category + ".html.php.tpl"), ("css", "bundle.css.tpl"), ("js", "bundle.js.tpl")]
        if category != "page":
            extensions.insert(0, ("php", "bundle.php.tpl"))
        for extension, source in extensions:
            planned[directory / (classname + "." + extension)] = template(source, replacements)

    if kind == "component":
        if len(names) == 1:
            names.insert(0, "Atoms")
        if names[0] not in {"Atoms", "Molecules", "Organisms", "Layouts"}:
            raise ValueError("Componentes devem usar Atoms, Molecules, Organisms ou Layouts.")
        classname = names[-1]
        namespace = "\\".join(["ComponentsCore", *names])
        bundle(root.joinpath("libs", "components-core", *names), namespace, classname, kind)
    else:
        manifest = json.loads((root / "composer.json").read_text(encoding="utf-8-sig"))
        candidates = []
        for namespace, directories in manifest.get("autoload", {}).get("psr-4", {}).items():
            for directory in directories if isinstance(directories, list) else [directories]:
                destination = root / directory
                if destination.resolve().is_relative_to(root / "src"):
                    candidates.append((namespace.rstrip("\\"), destination))
        if project:
            candidates = [item for item in candidates if item[0] == project.rstrip("\\")]
        if len(candidates) != 1:
            raise ValueError("Informe --project NAMESPACE para selecionar um unico mapeamento PSR-4 em src/ no composer.json.")
        project_namespace, project_root = candidates[0]
        parts(project_namespace)
        if kind == "layout":
            classname = suffixed(names[-1], "Layout")
            folder = [*names[:-1], classname]
            bundle(project_root.joinpath("Layouts", *folder), "\\".join([project_namespace, "Layouts", *folder]), classname, kind)
        else:
            if len(names) < 2:
                raise ValueError("Informe o programa e o nome, por exemplo Cursos/Resumo" + kind.title() + ".")
            program = names[:-1]
            program_root = project_root.joinpath("Programs", *program)
            program_namespace = "\\".join([project_namespace, "Programs", *program])
            if kind == "view":
                if page:
                    page_parts = parts(page)
                else:
                    base = names[-1]
                    if base.endswith("View"):
                        base = base[:-4]
                    elif base.endswith("Page"):
                        base = base[:-4]
                    page_parts = [base]
                if len(page_parts) != 1:
                    raise ValueError("--page recebe apenas o nome da Page no mesmo programa.")
                classname = suffixed(page_parts[0], "Page")
                layout_parts = parts(layout)
                layout_parts[-1] = suffixed(layout_parts[-1], "Layout")
                page_dir = program_root / "Pages" / classname
                page_namespace = program_namespace + "\\Pages\\" + classname
                layout_dir = project_root.joinpath("Layouts", *layout_parts)
                layout_namespace = "\\".join([project_namespace, "Layouts", *layout_parts])
                if not all((page_dir / (classname + ext)).exists() for ext in (".html.php", ".css", ".js")):
                    bundle(page_dir, page_namespace, classname, "page")
                if not (layout_dir / (layout_parts[-1] + ".php")).exists():
                    bundle(layout_dir, layout_namespace, layout_parts[-1], "layout")
            else:
                classname = suffixed(names[-1], "Page")
                bundle(program_root / "Pages" / classname, program_namespace + "\\Pages\\" + classname, classname, kind)
    for path in planned:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Destino fora do projeto: {path}")
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent.is_relative_to(root)):
            raise ValueError(f"Destino nao pode atravessar links simbolicos: {path}")
        if path.exists():
            raise ValueError(f"Arquivo ja existe; nada foi sobrescrito: {path.relative_to(root)}")
    created: list[Path] = []
    directories: list[Path] = []
    try:
        for path, content in planned.items():
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                directories.append(parent)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                stream.write(content)
    except OSError:
        for path in reversed(created):
            path.unlink()
        for directory in reversed(directories):
            directory.rmdir()
        raise
    return created


def main() -> int:
    banner = Path(__file__).resolve().with_name('banner.txt')
    if (len(sys.argv) == 1 or sys.argv[1] == 'help' or '--help' in sys.argv[1:] or '-h' in sys.argv[1:]) and banner.is_file():
        print(banner.read_text(encoding='utf-8-sig').rstrip('\r\n'), flush=True)
        print(flush=True)
    parser = argparse.ArgumentParser(description="Ferramentas do framework PHP: build e geracao de fontes.",
                                    epilog="Exemplos: uni help create view | uni create view Cursos/ResumoView | uni build")
    commands = parser.add_subparsers(dest="command")
    start = commands.add_parser('start', help='Mapear os projetos do workspace no Windows.')
    start.add_argument('path', nargs='?', type=Path, default=Path.cwd())
    start.add_argument('--offline', action='store_true', help='Somente mapear; sem validar Docker ou atualizar catalogo remoto.')
    use = commands.add_parser('use', help='Trocar o ambiente, atualizar branches Docker e recarregar containers.')
    use.add_argument('environment')
    use.add_argument('--editor', choices=['none', 'new', 'reuse'])
    for command, description in [('back', 'Selecionar o backend.'), ('front', 'Selecionar o frontend.'),
                                 ('up', 'Subir o ambiente ativo.'), ('down', 'Parar o ambiente ativo.'),
                                 ('status', 'Mostrar ambiente, projetos, branches e containers.'),
                                 ('where', 'Mostrar o caminho do projeto selecionado.'),
                                 ('projects', 'Listar projetos registrados.'), ('doctor', 'Validar Git, Docker, Compose e PHP.')]:
        commands.add_parser(command, help=description)
    push = commands.add_parser('push', help='Publicar commits do projeto selecionado ou do CLI.')
    push.add_argument('--message', '-m')
    push.add_argument('--cli', action='store_true', help='Publicar o repositorio uni-cli.')
    catalog_cmd = commands.add_parser('catalog', help='Sincronizar, publicar ou verificar o catalogo compartilhado.')
    catalog_cmd.add_argument('action', choices=['sync', 'publish', 'verify'])
    shell_cmd = commands.add_parser('shell', help='Instalar a funcao PowerShell que acompanha back/front/use.')
    shell_cmd.add_argument('action', choices=['install'])
    help_command = commands.add_parser("help", help="Mostrar ajuda geral ou de um comando.")
    help_command.add_argument("topic", nargs="*", help="Exemplos: help build; help create page.")
    create = commands.add_parser("create", help="Criar View, Layout, Page ou Component com templates padrao.")
    kinds = create.add_subparsers(dest="kind", required=True)
    for kind in ("view", "layout", "page", "component"):
        generator = kinds.add_parser(kind, aliases=["components"] if kind == "component" else [],
                                     help=f"Criar {kind} sem sobrescrever fontes existentes.")
        generator.add_argument("name", help="Ex.: Cursos/ResumoView, Cursos/ResumoPage, AppLayout ou Atoms/Alert.")
        if kind != "component":
            generator.add_argument("--project", help="Namespace PSR-4 do projeto; inferido quando ha apenas um em src/.")
        if kind == "view":
            generator.add_argument("--page", help="Nome da Page no mesmo programa; padrao: nome da View + Page.")
            generator.add_argument("--layout", default="AppLayout", help="Layout (padrao: AppLayout). Dependencias ausentes sao criadas.")
    validate = commands.add_parser("validate", help="Validar o arquivo guia.")
    validate.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Caminho do guia (padrao: libs_projects.json ao lado de uni.py).",
    )
    init = commands.add_parser("init", help="Criar composer.json e a estrutura do projeto.")
    init.add_argument("vendor", help="Prefixo Composer, por exemplo: uniube.")
    init.add_argument("project", help="Nome do projeto e namespace, por exemplo: AreaCandidato.")
    selection = init.add_mutually_exclusive_group()
    selection.add_argument("--libs", nargs='+', help="Nucleos do catalogo; dependencias sao incluidas automaticamente.")
    selection.add_argument("--no-framework", action='store_true', help="Criar projeto sem os nucleos do framework.")
    init.add_argument("--no-install", action='store_true', help="Preparar clones e manifesto sem instalar vendor/.")
    init.add_argument("--catalog", type=Path, default=DEFAULT_CONFIG, help="Catalogo de bibliotecas.")
    init.add_argument('--repository', help='Git vazio da nova aplicacao.')
    init.add_argument('--name', help='Identificador no catalogo.')
    init.add_argument('--path', type=Path)
    init.add_argument('--role', choices=['front', 'back'])
    init.add_argument('--environment')
    init.add_argument('--backend', help='Identificador do backend associado.')
    init.add_argument('--port', type=int)
    init.add_argument('--docker-repository')
    init.add_argument('--local', action='store_true', help='Criar localmente, sem publicar Git/catalogo.')
    packages = commands.add_parser('composer', help='Instalar, atualizar, adicionar, remover ou consultar bibliotecas.')
    packages.add_argument('--catalog', type=Path, default=DEFAULT_CONFIG)
    actions = packages.add_subparsers(dest='composer_action', required=True)
    actions.add_parser('install')
    update = actions.add_parser('update')
    update.add_argument('packages', nargs='*')
    add = actions.add_parser('add')
    add.add_argument('packages', nargs='+')
    add.add_argument('--version', help='Restricao de versao para bibliotecas externas.')
    remove = actions.add_parser('remove')
    remove.add_argument('packages', nargs='+')
    actions.add_parser('list')
    show = actions.add_parser('show')
    show.add_argument('package')
    search = actions.add_parser('search')
    search.add_argument('term')
    libs = commands.add_parser('libs', help='Listar catalogo, bibliotecas instaladas, versoes e commits.')
    libs.add_argument('--catalog', type=Path, default=DEFAULT_CONFIG)
    project = commands.add_parser('project', help='Gerenciar projetos do catalogo.')
    project_actions = project.add_subparsers(dest='project_action', required=True)
    register = project_actions.add_parser('register', help='Consultar um Git e publicar seu registro no catalogo.')
    register.add_argument('repository', help='URL HTTPS ou SSH do repositorio Git.')
    register.add_argument('--path', type=Path, help='Manifesto local opcional; por padrao consulta o Git remoto.')
    register.add_argument('--name', help='Identificador no catalogo; padrao: nome do repositorio.')
    register.add_argument('--catalog', type=Path, default=DEFAULT_CONFIG)
    register.add_argument('--role', choices=['front', 'back'])
    register.add_argument('--environment')
    register.add_argument('--backend')
    register.add_argument('--docker-repository')
    register.add_argument('--local', action='store_true', help='Salvar sem publicar o catalogo.')
    clone = project_actions.add_parser('clone', help='Clonar um projeto cadastrado na raiz do workspace e instalar dependencias.')
    clone.add_argument('name')
    clone.add_argument('--no-install', action='store_true')
    commands.add_parser("build", help="Compilar rotas, templates, componentes e assets.")
    docker_cmd = commands.add_parser("docker", help="Executar Docker Compose para o projeto atual.")
    docker_cmd.add_argument("arguments", nargs=argparse.REMAINDER, help="Argumentos do Compose, por exemplo up -d.")
    cache_cmd = commands.add_parser("cache", help="Gerenciar o cache de respostas.")
    cache_sub = cache_cmd.add_subparsers(dest="cache_action")
    cache_clear = cache_sub.add_parser("clear", help="Limpar o cache de respostas.")
    cache_clear.add_argument("group", nargs="?", default=None, help="Grupo/página específica a limpar (opcional).")
    args = parser.parse_args()

    if args.command is None or args.command == "help":
        topic = getattr(args, "topic", [])
        if not topic:
            parser.print_help()
        elif topic[0] in commands.choices and len(topic) == 1:
            parser.parse_args([*topic, "--help"])
        elif topic[0] in ('composer', 'project') and len(topic) == 2:
            parser.parse_args([*topic, '--help'])
        elif topic[0] == "create" and (len(topic) == 1 or (len(topic) == 2 and topic[1] in {"view", "layout", "page", "component", "components"})):
            parser.parse_args([*topic, "--help"])
        else:
            parser.error("Topico de ajuda desconhecido.")
        return 0

    try:
        from uni_workspace import Workspace, current_project, doctor, push_project
        from uni_projects import register_and_publish, sync_catalog, publish_catalog
        if args.command == 'shell':
            from uni_shell import install
            install()
            return 0
        if args.command == 'doctor':
            from uni_runtime import run_tool
            doctor(); run_tool(Path.cwd(), 'php', ['-v'])
            return 0
        if args.command == 'start':
            if not args.offline:
                doctor(); sync_catalog(DEFAULT_CONFIG)
                from uni_runtime import run_tool
                run_tool(args.path.resolve(), 'php', ['-v'])
            ws = Workspace(args.path)
            projects = ws.scan()
            ws.adopt_running()
            print(f'Workspace mapeado: {ws.root} ({len(projects)} projetos).')
            for name, item in projects.items(): print(name + ' -> ' + item['path'])
            return 0
        if args.command == 'projects':
            config_data = load_config(DEFAULT_CONFIG)
            environments = {}
            for name, item in config_data.get('projects', {}).items():
                env = item.get('environment', 'Sem ambiente')
                if env not in environments:
                    environments[env] = []
                environments[env].append((name, item))
            
            for env, projs in environments.items():
                print(f"Ambiente: {env} (para usar digite: uni use {env})")
                for p_name, p_item in projs:
                    role = p_item.get('role', 'desconhecido')
                    print(f"  - {p_name} ({role}) | {p_item.get('repository', '')}")
                print()
            return 0
        if args.command in ('use', 'back', 'front', 'up', 'down', 'status', 'where'):
            ws = Workspace()
            if args.command == 'use':
                doctor(); ws.switch(args.environment); ws.editor(args.editor)
            elif args.command in ('back', 'front'): ws.select(args.command)
            elif args.command in ('up', 'down'): doctor(); ws.operate(args.command)
            elif args.command == 'where': print(ws.selected_path())
            else: ws.status()
            return 0
        if args.command == 'push':
            push_project(Path(__file__).resolve().parent if args.cli else current_project(), args.message)
            return 0
        if args.command == 'catalog':
            if args.action == 'sync': sync_catalog(DEFAULT_CONFIG)
            elif args.action == 'publish': publish_catalog(DEFAULT_CONFIG)
            else:
                from urllib.request import urlopen
                data = load_config(DEFAULT_CONFIG)
                with urlopen(data['catalog_url'], timeout=20) as response: published = json.load(response)
                if published != data: raise ValueError('JSON publicado ainda difere do catalogo local.')
                print('Catalogo publicado confere com o local.')
            return 0
        if args.command == 'project':
            if args.project_action == 'clone':
                from uni_workspace import clone_project
                clone_project(Workspace(), args.name, args.no_install)
            else:
                name = register_and_publish(args, load_config)
                print(f"Projeto '{name}' registrado em {args.catalog.resolve()}.")
            return 0
        if args.command in ('composer', 'libs'):
            try: project_root = current_project()
            except ValueError:
                if args.command != 'libs': raise
                project_root = Path.cwd()
            manager = Packages(project_root, args.catalog)
            action = 'list' if args.command == 'libs' else args.composer_action
            if action == 'list': manager.listing()
            elif action == 'show': manager.show(args.package)
            elif action == 'search': manager.composer(['search', '--', args.term])
            elif action == 'install': manager.install()
            else: manager.change(action, args.packages, getattr(args, 'version', None))
            return 0
        if args.command == "docker":
            ws = Workspace()
            if not ws.state.get('selected'): raise ValueError('Selecione um ambiente com uni use.')
            ws.compose(ws.state['selected'], args.arguments or ['ps'])
            return 0
        if args.command in ('create', 'build', 'cache'):
            os.chdir(current_project())
        if args.command == "create":
            created = scaffold(args.kind, args.name, Path.cwd(), getattr(args, "project", None),
                               getattr(args, "page", None), getattr(args, "layout", "AppLayout"))
            for path in created:
                print(f"Criado: {path.relative_to(Path.cwd())}")
            print("Execute uni build para compilar.")
            if args.kind == "view":
                print("Use $this->response->render(new \\FrontendCore\\View\\View(page: '.../Page.html.php', data: [...])) no controller.")
            return 0
        if args.command == "build":
            count = build_routes(Path.cwd())
            print(f"Build concluido: {count} rota(s) em routes.json; manifestos em storage/framework/manifest. Etapas executadas conforme os modulos instalados.")
            return 0
        if args.command == "cache":
            if getattr(args, "cache_action", None) == "clear":
                if not Path('/.dockerenv').exists():
                    from uni_runtime import run_tool
                    arguments = ['-B', str(Path(__file__).resolve()), 'cache', 'clear']
                    if args.group: arguments.append(args.group)
                    run_tool(Path.cwd(), 'python3', arguments)
                    return 0
                cache_dir = Path.cwd() / "storage" / "framework" / "cache"
                target = cache_dir / args.group if args.group else cache_dir
                if args.group and (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', args.group)
                                   or not target.resolve().is_relative_to(cache_dir.resolve())):
                    raise ValueError('Grupo de cache invalido.')
                if not target.exists():
                    print(f"Diretório de cache não encontrado: {target.relative_to(Path.cwd())}")
                    return 0
                count = 0
                if args.group:
                    for item in list(target.rglob("*"))[::-1]:
                        if item.is_file():
                            item.unlink()
                            count += 1
                        elif item.is_dir():
                            item.rmdir()
                    if target.exists():
                        target.rmdir()
                    print(f"Cache do grupo '{args.group}' limpo ({count} arquivo(s) removido(s)).")
                else:
                    for item in list(cache_dir.rglob("*"))[::-1]:
                        if item.name == ".cache.lock":
                            continue
                        if item.is_file():
                            item.unlink()
                            count += 1
                        elif item.is_dir():
                            try:
                                item.rmdir()
                            except OSError:
                                pass
                    print(f"Cache geral limpo ({count} arquivo(s) removido(s)).")
                return 0
        if args.command == "init":
            from uni_init import initialize
            initialize(args, init_project, select_libraries, load_config)
            return 0
        config = load_config(args.config)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    print(
        f"Arquivo guia valido: {len(config['libs'])} biblioteca(s), "
        f"{len(config['projects'])} projeto(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
