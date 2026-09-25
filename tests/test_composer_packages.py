import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uni_packages import Packages, read_json, write_json
from uni_distribution import prepare_distribution
import uni_build
import uni


class ComposerPackages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / 'catalog.json'
        write_json(self.catalog, {'libs': {
            'application': {'composer_name': 'uniube/application-core', 'root_name': 'application-core',
                            'repository': 'https://example.invalid/application.git', 'dependencies': []},
            'frontend': {'composer_name': 'uniube/frontend-core', 'root_name': 'frontend-core',
                         'repository': 'https://example.invalid/frontend.git', 'dependencies': ['application']},
        }})
        write_json(self.root / 'composer.json', {'name': 'test/project', 'type': 'project',
                                               'autoload': {'psr-4': {'App\\': 'src/'}},
                                               'extra': {'custom': 'keep'}})
        self.manager = Packages(self.root, self.catalog)

    def add_frontend(self):
        self.manager.change('add', ['frontend'], no_install=True)

    def test_configure_no_clone_and_no_symlink(self):
        with patch('uni_packages.subprocess.run') as execute:
            self.add_frontend()
            execute.assert_not_called()
        manifest = read_json(self.root / 'composer.json')
        self.assertIn('uniube/application-core', manifest['require'])
        self.assertEqual(manifest['extra']['installer-paths']['libs/frontend-core/'], ['uniube/frontend-core'])
        self.assertEqual(manifest['autoload']['psr-4']['FrontendCore\\'], 'libs/frontend-core/')
        self.assertEqual(manifest['autoload']['psr-4']['App\\'], 'src/')
        self.assertEqual(manifest['extra']['custom'], 'keep')
        self.assertFalse(manifest['config']['source-fallback'])
        self.assertTrue(all(r['type'] == 'vcs' for r in manifest['repositories']))
        paths = manifest['extra']['installer-paths']
        self.assertEqual(list(paths)[-1], 'vendor/{$vendor}/{$name}/')
        self.assertEqual(paths['vendor/{$vendor}/{$name}/'], ['type:library'])

    def test_migrate_metadata_and_path_repository(self):
        manifest = read_json(self.root / 'composer.json')
        entry = {**self.manager.catalog['application'], 'ref': 'old'}
        manifest['extra']['uni'] = {'libraries': {'application': entry}}
        manifest['repositories'] = [{'type': 'path', 'url': 'libs/*'}, {'type': 'composer', 'url': 'https://example.invalid'}]
        write_json(self.root / 'composer.json', manifest)
        self.manager.configure_project()
        result = read_json(self.root / 'composer.json')
        self.assertNotIn('ref', result['extra']['uni']['libraries']['application'])
        self.assertEqual(result['require']['uniube/application-core'], 'dev-main')
        self.assertFalse(any(r['type'] == 'path' for r in result['repositories']))
        self.assertEqual(result['repositories'][0]['type'], 'composer')

    def test_update_resolves_before_install_and_targets_only_libraries(self):
        self.add_frontend()
        write_json(self.root / 'composer.lock', {'packages': []})
        with patch.object(self.manager, 'composer') as composer:
            self.manager.change('update', [])
        resolve, install = [c.args[0] for c in composer.call_args_list]
        self.assertEqual(resolve[0], 'update')
        self.assertIn('uniube/frontend-core', resolve)
        self.assertIn('--no-install', resolve)
        self.assertNotIn('--no-plugins', install)
        self.assertEqual(install[0], 'install')

    def test_resolution_failure_restores_manifest_and_lock(self):
        before = (self.root / 'composer.json').read_bytes()
        write_json(self.root / 'composer.lock', {'packages': []})
        lock = (self.root / 'composer.lock').read_bytes()
        with patch.object(self.manager, 'composer', side_effect=ValueError('offline')):
            with self.assertRaisesRegex(ValueError, 'offline'):
                self.manager.change('add', ['frontend'])
        self.assertEqual((self.root / 'composer.json').read_bytes(), before)
        self.assertEqual((self.root / 'composer.lock').read_bytes(), lock)

    def test_install_failure_keeps_resolved_configuration(self):
        with patch.object(self.manager, 'composer', side_effect=[None, ValueError('download failed')]):
            with self.assertRaisesRegex(ValueError, 'download failed'):
                self.manager.change('add', ['frontend'])
        self.assertIn('uniube/frontend-core', read_json(self.root / 'composer.json')['require'])

    def test_nested_git_preserved_and_blocks_install(self):
        self.add_frontend()
        git = self.root / 'libs/frontend-core/.git'
        git.mkdir(parents=True)
        with patch.object(self.manager, 'composer') as composer:
            with self.assertRaisesRegex(ValueError, 'Clone Git'):
                self.manager.install()
            composer.assert_not_called()
        self.assertTrue(git.exists())

    def test_dirty_source_blocks_update(self):
        self.add_frontend()
        (self.root / '.git').mkdir()
        with patch('uni_packages.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=' M libs/frontend-core/Renderer.php\n')):
            with self.assertRaisesRegex(ValueError, 'Fontes modificadas'):
                self.manager.change('update', [])

    def test_remove_required_core_blocked_before_composer(self):
        self.add_frontend()
        with patch.object(self.manager, 'composer') as composer:
            with self.assertRaisesRegex(ValueError, 'dependente'):
                self.manager.change('remove', ['application'])
            composer.assert_not_called()

    def test_distribution_idempotent_and_cache_ignored(self):
        (self.root / '.gitignore').write_text('/libs/\n/vendor/\n/storage/\n*.compiled.php\n/custom-secret/\n')
        prepare_distribution(self.root)
        first = (self.root / '.gitignore').read_bytes()
        prepare_distribution(self.root)
        self.assertEqual(first, (self.root / '.gitignore').read_bytes())
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for path, ignored in [('libs/a/A.php', False), ('vendor/autoload.php', False),
                              ('storage/framework/manifest/build.php', False),
                              ('storage/framework/assets/versions.json', False),
                              ('storage/framework/cache/a.json', True), ('storage/logs/a.log', True),
                              ('application-core-dev/A.php', True), ('auth.json', True), ('custom-secret/a', True)]:
            result = subprocess.run(['git', 'check-ignore', '-q', path], cwd=self.root)
            self.assertEqual(result.returncode == 0, ignored, path)

    @unittest.skipIf(sys.platform == 'win32', 'Lock fcntl validado no worker Linux')
    def test_failed_update_blocks_compilation(self):
        self.add_frontend()
        with patch('uni_packages.Packages.change', side_effect=ValueError('offline')), patch('uni_build._build_routes') as compile:
            with self.assertRaisesRegex(ValueError, 'offline'):
                uni_build.build_routes(self.root)
            compile.assert_not_called()


if __name__ == '__main__': unittest.main()
