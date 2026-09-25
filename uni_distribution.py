"""Arquivos que acompanham a publicacao pronta; dados de execucao ficam locais."""
from pathlib import Path

START = '# BEGIN UNI PUBLICATION'
END = '# END UNI PUBLICATION'
RULES = '''/storage/*
!/storage/framework/
/storage/framework/*
!/storage/framework/manifest/
/storage/framework/manifest/*
!/storage/framework/manifest/build.php
!/storage/framework/manifest/assets.php
!/storage/framework/manifest/components.php
!/storage/framework/assets/
/.uni-*
/.uni/
/.compiler-build-*/
/.compiler.lock
/scratch/
/uni-cli/
/docker/
/docker-front/
/docker-back/
/*-core-dev/
/dev-libs/
__pycache__/
*.pyc
.env
.env.*
!.env.example
auth.json
/tests/.tmp/
!/composer.lock
'''


def prepare_distribution(root):
    root = Path(root).resolve()
    file = root / '.gitignore'
    content = file.read_text(encoding='utf-8-sig') if file.exists() else ''
    if START in content and END in content:
        before, rest = content.split(START, 1)
        content = before + rest.split(END, 1)[1]
    old = {'/vendor/', '/libs/', '/storage/', '/public/assets/', '*.compiled.php',
           'vendor/', 'libs/', 'storage/', 'public/assets/'}
    lines = [line for line in content.splitlines() if line.strip() not in old]
    # Put publication rules last so the old broad rules do not hide build manifests.
    content = '\n'.join(lines).rstrip() + '\n\n' + START + '\n' + RULES + END + '\n'
    if not file.exists() or file.read_text(encoding='utf-8-sig') != content:
        file.write_text(content, encoding='utf-8')
    # Composer restores package .gitignore files on each install/update.
    # Compiled PHP must travel with the application in this deployment model.
    for ignore in (root / 'libs').glob('*/.gitignore'):
        if ignore.is_symlink() or not ignore.resolve().is_relative_to(root):
            raise ValueError('Ignore de biblioteca fora do projeto: ' + str(ignore))
        original = ignore.read_text(encoding='utf-8-sig')
        updated = '\n'.join(line for line in original.splitlines() if line.strip() != '*.compiled.php') + '\n'
        if updated != original: ignore.write_text(updated, encoding='utf-8')
