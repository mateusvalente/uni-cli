"""Execucao de PHP/Composer no Linux, orquestrada pelo Python do host."""
import os
import subprocess
from pathlib import Path

CLI = Path(__file__).resolve().parent


def container_command(root, executable, arguments):
    root = Path(root).resolve()
    if Path('/.dockerenv').exists():
        return [executable, *map(str, arguments)], os.environ.copy()
    # Um Compose de ferramentas independente do ambiente ativo permite init/build
    # antes de subir o servidor ou mesmo sem um checkout Docker do projeto.
    command = ['docker', 'compose', '-f', str(CLI / '.dist/host/compose.yaml'),
               'run', '--rm', '--no-deps', '--entrypoint', executable, 'tools']
    mapped = []
    for argument in map(str, arguments):
        path = Path(argument)
        if path.is_absolute() and path.is_relative_to(root):
            argument = '/var/www/html/' + path.relative_to(root).as_posix()
        elif path.is_absolute() and path.is_relative_to(CLI):
            argument = '/opt/uni-cli/' + path.relative_to(CLI).as_posix()
        mapped.append(argument.rstrip('/') if argument.startswith('/var/www/html/') else argument)
    return [*command, *mapped], {**os.environ, 'UNI_PROJECT_PATH': str(root), 'UNI_CLI_PATH': str(CLI)}


def run_tool(root, executable, arguments, capture=False):
    command, environment = container_command(root, executable, arguments)
    result = subprocess.run(command, cwd=root, env=environment, capture_output=capture,
                            text=True, encoding='utf-8')
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip() if capture else 'Consulte a saida acima.'
        raise ValueError(f'{executable} falhou no ambiente Linux. {detail}')
    return result
