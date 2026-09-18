"""Instala somente o carregamento da funcao uni, preservando o perfil PowerShell."""
import shutil
import subprocess
from pathlib import Path


def install():
    script = Path(__file__).resolve().with_name('uni-shell.ps1')
    line = ". '" + str(script).replace("'", "''") + "' # uni-cli integration"
    found = False
    for shell in ('powershell', 'pwsh'):
        binary = shutil.which(shell)
        if not binary: continue
        found = True
        policy = subprocess.run([binary, '-NoProfile', '-Command', 'Get-ExecutionPolicy'],
                                capture_output=True, text=True, check=True).stdout.strip()
        if policy in ('Restricted', 'AllSigned'):
            raise ValueError(f'{shell}: politica {policy} impede carregar a funcao local. '
                             'O uni.cmd continua disponivel. Ajuste a politica conscientemente antes de instalar a integracao.')
        result = subprocess.run([binary, '-NoProfile', '-Command', '$PROFILE.CurrentUserAllHosts'],
                                capture_output=True, text=True, check=True)
        profile = Path(result.stdout.strip())
        content = profile.read_text(encoding='utf-8-sig') if profile.exists() else ''
        lines = [entry for entry in content.splitlines() if not entry.endswith('# uni-cli integration')]
        profile.parent.mkdir(parents=True, exist_ok=True)
        if profile.exists() and not profile.with_suffix(profile.suffix + '.uni-backup').exists():
            shutil.copy2(profile, profile.with_suffix(profile.suffix + '.uni-backup'))
        profile.write_text('\n'.join([*lines, line, '']), encoding='utf-8-sig')
        print('Integracao instalada: ' + str(profile))
    if not found: raise ValueError('PowerShell nao encontrado.')
    print('Abra um novo terminal ou carregue: ' + line)
