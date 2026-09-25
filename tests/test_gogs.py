import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uni_gogs
from uni_packages import Packages, write_json


class GogsArchives(unittest.TestCase):
    def test_archive_and_locked_revision_survive_branch_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upstream = root / 'upstream'
            upstream.mkdir()
            run = subprocess.run
            def git(*args):
                return run(['git', *args], cwd=upstream, check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-b', 'master')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            write_json(upstream / 'composer.json', {'name': 'uniube/example', 'type': 'library'})
            git('add', 'composer.json')
            git('commit', '-m', 'initial')
            first = git('rev-parse', 'HEAD')
            entry = {'repository': 'https://example.invalid/test.git', 'composer_name': 'uniube/example', 'branch': 'master'}
            def local_transport(command, **kwargs):
                command = [str(upstream) if arg == entry['repository'] else arg for arg in command]
                return run(command, **kwargs)
            with patch('uni_gogs.subprocess.run', side_effect=local_transport):
                metadata = uni_gogs.package(entry, root)['package']
                archive = root / metadata['dist']['url']
                with zipfile.ZipFile(archive) as stream:
                    self.assertEqual(json.loads(stream.read('composer.json'))['name'], 'uniube/example')
                    self.assertFalse(any(name.startswith('.git/') for name in stream.namelist()))
                archive.unlink()
                (upstream / 'new.txt').write_text('later')
                git('add', 'new.txt')
                git('commit', '-m', 'later')
                locked = uni_gogs.package(entry, root, reference=first)['package']
                self.assertEqual(locked['source']['reference'], first)
                with zipfile.ZipFile(root / locked['dist']['url']) as stream:
                    self.assertNotIn('new.txt', stream.namelist())

    def test_rejects_credentials_in_repository_url(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'sem credenciais'):
                uni_gogs.package({'repository': 'https://user:secret@example.invalid/a.git'}, root)

    def test_install_reconstructs_exact_missing_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            entry = {'provider': 'gogs', 'composer_name': 'uniube/example', 'repository': 'https://example.invalid/a.git'}
            item = {'name': entry['composer_name'], 'source': {'url': entry['repository'], 'reference': 'a' * 40},
                    'dist': {'url': '.uni/packages/example.zip'}}
            write_json(root / 'composer.lock', {'packages': [item]})
            write_json(root / 'catalog.json', {'libs': {}})
            manager = Packages(root, root / 'catalog.json')
            with patch('uni_auth.composer_environment', return_value={}), patch('uni_gogs.package', return_value={'package': item}) as archive:
                manager.prepare_locked_archives({'example': entry})
            archive.assert_called_once_with(entry, root.resolve(), reference='a' * 40)


if __name__ == '__main__':
    unittest.main()
