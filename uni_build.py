"""Worker de build: executado exclusivamente no Linux do Docker."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

def build_routes(root: Path) -> int:
    """Serializa a publicacao para que dois builds nao disputem a mesma versao."""
    with (root / '.uni-build.lock').open('a+b') as lock:
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
        from uni_runtime import run_tool
        return run_tool(root, command[0], command[1:], capture=True)

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


if __name__ == '__main__':
    try:
        if not Path('/.dockerenv').exists():
            raise ValueError('Execute uni build; este worker requer o container Linux.')
        print(json.dumps({'routes': build_routes(Path(sys.argv[1]))}))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
