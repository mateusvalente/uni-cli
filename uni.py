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
    """Serializa a publicacao para que dois builds nao disputem a mesma versao."""
    with (root / '.uni-build.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Outro build esta em andamento neste projeto.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('Outro build esta em andamento neste projeto.') from exc
        return _build_routes(root)


def _build_routes(root: Path) -> int:
    """Usa Composer e Reflection no build; publica o JSON apenas apos sucesso."""
    root = root.resolve()
    if not (root / "composer.json").is_file():
        raise ValueError("composer.json nao encontrado. Inicialize o projeto antes do build.")
    if not (root / "src").is_dir():
        raise ValueError("A pasta src nao foi encontrada.")
    output = root / "routes.json"
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("routes.json deve ser um arquivo regular.")

    compiler = Path(__file__).resolve().parent / ".dist" / "uni" / "build_routes.php"
    def run(command: list[str]) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(f"'{command[0]}' nao encontrado. Execute o build pelo servico uni do Docker.") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ValueError(f"Falha ao executar {command[0]}:\n{detail}")
        return result

    run(["composer", "dump-autoload", "--optimize", "--strict-psr", "--strict-ambiguous", "--no-scripts", "--no-plugins", "--no-interaction"])
    with tempfile.TemporaryDirectory(dir=root, prefix=".uni-build-") as stage_name:
        stage = Path(stage_name).resolve()
        if not stage.is_relative_to(root):
            raise ValueError("Diretorio temporario fora do projeto.")
        versions = root / "storage/framework/assets"
        if versions.is_dir():
            index_file = versions / "versions.json"
            if index_file.is_file():
                index = json.loads(index_file.read_text(encoding="utf-8"))
                migrated = {}
                for name, entry in index.get("assets", {}).items():
                    if name.startswith(("components/", "views/")):
                        continue
                    if name.startswith("pages/"):
                        filename = name.rsplit("/", 1)[-1]
                        page_name = filename.split(".", 1)[0]
                        slug = re.sub(r"(?<!^)([A-Z])", r"-\1", page_name).lower()
                        name = slug + "/" + filename
                    if name in migrated:
                        raise ValueError("Nome de page duplicado no historico de assets: " + name)
                    migrated[name] = entry
                index["assets"] = migrated
                index.pop("files", None)
                staged_index = stage / "storage/framework/assets/versions.json"
                staged_index.parent.mkdir(parents=True)
                staged_index.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
        result = run(["php", str(compiler), str(root), str(stage)])
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("O compilador PHP nao retornou um JSON valido.") from exc
        if not isinstance(document, dict) or not isinstance(document.get("routes"), list):
            raise ValueError("O compilador PHP retornou um formato de rotas invalido.")

        runtime = root / "storage/framework"
        manifest_dir = runtime / "manifest"
        for directory in [root / "storage", runtime, manifest_dir]:
            if directory.is_symlink() or not directory.resolve().is_relative_to(root):
                raise ValueError("O cache de build deve ficar dentro do projeto.")
            directory.mkdir(exist_ok=True)
        artifacts = json.loads((stage / "artifacts.json").read_text(encoding="utf-8"))
        for name, relative in artifacts.items():
            destination = root / relative
            if not destination.resolve().is_relative_to(root) or not (
                relative.startswith(("storage/framework/", "public/assets/")) or ".compiled." in destination.name
            ):
                raise ValueError("Destino de compilacao invalido ou fora do projeto.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            prepared = stage / "artifacts" / name
            if not destination.is_file() or destination.read_bytes() != prepared.read_bytes():
                os.replace(prepared, destination)
        with (stage / "routes.json").open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        # O manifesto ativo só aponta para templates completamente compilados.
        os.replace(stage / "components.php", manifest_dir / "components.php")
        os.replace(stage / "assets.php", manifest_dir / "assets.php")
        os.replace(stage / "build.php", manifest_dir / "build.php")
        os.replace(stage / "routes.json", output)
    return len(document["routes"])


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
    parser = argparse.ArgumentParser(description="Ferramentas do framework PHP: build e geracao de fontes.",
                                    epilog="Exemplos: uni help create view | uni create view Cursos/ResumoView | uni build")
    commands = parser.add_subparsers(dest="command")
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
        elif topic[0] in {"init", "validate", "build", "cache", "docker", "composer", "libs"} and len(topic) == 1:
            parser.parse_args([*topic, "--help"])
        elif topic[0] == 'composer' and len(topic) == 2:
            parser.parse_args([*topic, '--help'])
        elif topic[0] == "create" and (len(topic) == 1 or (len(topic) == 2 and topic[1] in {"view", "layout", "page", "component", "components"})):
            parser.parse_args([*topic, "--help"])
        else:
            parser.error("Topico de ajuda desconhecido.")
        return 0

    try:
        if args.command in ('composer', 'libs'):
            manager = Packages(Path.cwd(), args.catalog)
            action = 'list' if args.command == 'libs' else args.composer_action
            if action == 'list': manager.listing()
            elif action == 'show': manager.show(args.package)
            elif action == 'search': manager.composer(['search', '--', args.term])
            elif action == 'install': manager.install()
            else: manager.change(action, args.packages, getattr(args, 'version', None))
            return 0
        if args.command == "docker":
            root = Path.cwd().resolve()
            manifest = json.loads((root / "composer.json").read_text(encoding="utf-8-sig"))
            name = manifest.get("name", "").split("/")[-1]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                raise ValueError("O nome Composer deve terminar com um nome valido para o Docker.")
            port = manifest.get("extra", {}).get("uni", {}).get("docker", {}).get("port", 8080)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("extra.uni.docker.port deve ser um inteiro entre 1 e 65535.")
            compose = Path(__file__).resolve().parents[1] / "docker/compose.yaml"
            if not compose.is_file():
                raise ValueError("Ambiente Docker nao encontrado ao lado de uni-cli.")
            environment = {**os.environ, "PROJECT_PATH": str(root), "COMPOSE_PROJECT_NAME": name, "APP_PORT": str(port)}
            return subprocess.run(["docker", "compose", "-f", str(compose), *(args.arguments or ["up", "-d"])], env=environment).returncode
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
                cache_dir = Path.cwd() / "storage" / "framework" / "cache"
                target = cache_dir / args.group if args.group else cache_dir
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
            manager = Packages(Path.cwd(), args.catalog)
            selected = select_libraries(args)
            closure = manager.closure(selected)
            name = init_project(args.vendor, args.project, Path.cwd(), framework='application' in closure)
            gitignore = Path.cwd() / '.gitignore'
            if not gitignore.exists():
                gitignore.write_text('/vendor/\n/libs/\n/storage/\n/public/assets/\n/.uni-*\n*.compiled.php\n__pycache__/\n', encoding='utf-8')
            if selected:
                manager.change('add', selected, no_install=args.no_install)
            print(f"Projeto '{name}' inicializado.")
            print("Criados: composer.json, src/Core, src/Programs e src/Config.")
            print("public/index.php preparado (arquivo existente preservado).")
            return 0
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    print(
        f"Arquivo guia valido: {len(config['libs'])} biblioteca(s), "
        f"{len(config['projects'])} projeto(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
