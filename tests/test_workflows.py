import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uni
import uni_workspace as work
import uni_projects as projects
from uni_packages import write_json


class Workflows(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ.get('UNI_TEST_TMP', str(Path(__file__).parent / '.tmp')))
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.catalog = self.root / 'catalog.json'
        write_json(self.catalog, {'libs': {}, 'projects': {}})
        self.pointer = patch.object(work, 'LOCAL', self.root / 'local.json')
        self.pointer.start(); self.addCleanup(self.pointer.stop)

    def project(self, name, relative=None):
        folder = self.root / (relative or ('projetos/' + name))
        folder.mkdir(parents=True)
        write_json(folder / 'composer.json', {'name': 'example/' + name, 'type': 'project'})
        return folder

    def test_discovery_selection_and_duplicates(self):
        front = self.project('candidate-front')
        back = self.project('candidate-back')
        self.project('ignored', 'vendor/ignored')
        ws = work.Workspace(self.root, self.catalog)
        self.assertEqual(set(ws.scan()), {'candidate-front', 'candidate-back'})
        ws.state['active'] = 'candidate'; ws.select('back')
        self.assertEqual(ws.selected_path(), back)
        ws.select('front'); self.assertEqual(ws.selected_path(), front)
        self.project('candidate-front', 'duplicate')
        before = ws.file.read_bytes()
        with self.assertRaisesRegex(ValueError, 'duplicado'): ws.scan()
        self.assertEqual(before, ws.file.read_bytes())

    def test_missing_related_backend_blocks_switch_before_docker(self):
        self.project('candidate-front')
        write_json(self.catalog, {'libs': {}, 'projects': {'candidate-front': {
            'composer_name': 'example/candidate-front', 'related': {'backend': 'api'}}}})
        ws = work.Workspace(self.root, self.catalog); ws.scan()
        with patch.object(ws, 'prepare') as prepare:
            with self.assertRaisesRegex(ValueError, 'ausente'): ws.switch('candidate')
            prepare.assert_not_called()

    def test_default_editor_does_nothing(self):
        ws = work.Workspace(self.root, self.catalog)
        with patch('builtins.input', return_value=''), patch.object(work, 'sys_stdin_tty', return_value=True), patch.object(work.subprocess, 'run') as run:
            ws.editor(); run.assert_not_called()

    def test_remote_registration_preserves_associations_and_rejects_duplicates(self):
        response = SimpleNamespace(returncode=0, stdout='ref: refs/heads/main\tHEAD\n')
        with patch.object(projects, 'remote_manifest', return_value={'name': 'example/api'}), patch.object(projects.subprocess, 'run', return_value=response):
            projects.register_project('https://github.com/example/api.git', None, self.catalog, None, uni.load_config)
            data = json.loads(self.catalog.read_text()); data['projects']['api']['docker'] = {'branch': 'api'}
            write_json(self.catalog, data)
            projects.register_project('git@github.com:example/api.git', None, self.catalog, None, uni.load_config)
            self.assertEqual(json.loads(self.catalog.read_text())['projects']['api']['docker']['branch'], 'api')
            before = self.catalog.read_bytes()
            with self.assertRaises(ValueError): projects.register_project('https://github.com/other/api.git', None, self.catalog, None, uni.load_config)
            self.assertEqual(before, self.catalog.read_bytes())

    def test_build_host_only_dispatches_worker(self):
        with patch.object(uni.Path, 'exists', return_value=False), patch('uni_runtime.run_tool', return_value=SimpleNamespace(stdout='{"routes": 9}')) as dispatch:
            self.assertEqual(uni.build_routes(self.root), 9)
            self.assertEqual(dispatch.call_args.args[1], 'python3')
            self.assertIn('uni_build.py', dispatch.call_args.args[2][1])
            self.assertFalse((self.root / '.uni-build.lock').exists())

    def git(self, root, *args):
        return subprocess.run(['git', *map(str, args)], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    def repo(self, path):
        path.mkdir()
        self.git(path, 'init', '-b', 'main')
        self.git(path, 'config', 'user.name', 'CLI Test')
        self.git(path, 'config', 'user.email', 'cli-test@example.invalid')

    def test_publish_only_catalog_and_verify_remote(self):
        remote = self.root / 'remote.git'; self.git(self.root, 'init', '--bare', remote)
        repo = self.root / 'cli'; self.repo(repo)
        catalog = repo / 'libs_projects.json'; write_json(catalog, {'libs': {}, 'projects': {}})
        (repo / 'tool.py').write_text('original')
        self.git(repo, 'add', '.'); self.git(repo, 'commit', '-m', 'Initial')
        self.git(repo, 'remote', 'add', 'origin', remote); self.git(repo, 'push', '-u', 'origin', 'main')
        write_json(catalog, {'libs': {}, 'projects': {'test': {'name': 'test'}}})
        (repo / 'tool.py').write_text('unrelated change')
        projects.publish_catalog(catalog)
        published = json.loads(self.git(remote, 'show', 'main:libs_projects.json'))
        self.assertIn('test', published['projects'])
        self.assertEqual(self.git(remote, 'show', 'main:tool.py'), 'original')
        self.assertIn('tool.py', self.git(repo, 'status', '--short'))

    def test_dirty_docker_preflight_does_not_stop_environment(self):
        self.project('candidate-front')
        ws = work.Workspace(self.root, self.catalog); ws.scan()
        with patch.object(ws, 'prepare', side_effect=ValueError('alteracoes locais')), patch.object(ws, 'compose') as compose:
            with self.assertRaises(ValueError): ws.switch('candidate')
            compose.assert_not_called()
            self.assertIsNone(ws.state['active'])

    def test_init_creates_host_project_and_updates_map(self):
        import uni_init
        ws = work.Workspace(self.root, self.catalog); ws.scan()
        args = SimpleNamespace(catalog=self.catalog, local=True, vendor='example', project='NewProject',
                               name='new-project', role='front', environment='new-project', port=8099,
                               backend=None, path=None, repository=None, docker_repository=None,
                               no_install=True, no_framework=True, libs=None)
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        root = self.root / 'projetos/new-project'
        self.assertTrue((root / 'public/index.php').is_file())
        self.assertEqual(json.loads((root / 'composer.json').read_text())['autoload']['psr-4'], {'NewProject\\': 'src/'})
        self.assertNotIn('ApplicationCore', (root / 'public/index.php').read_text())
        self.assertIn('new-project', ws.state['projects'])
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws):
            with self.assertRaisesRegex(ValueError, 'cadastrado'):
                uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)

    def test_docker_branch_is_derived_from_main_without_changing_it(self):
        import uni_init
        template = self.root / 'template'; self.repo(template)
        for name, content in [('uni/Dockerfile', 'FROM php:8.5-cli-alpine\n'),
                              ('php/start.sh', '#!/bin/sh\nexec php-fpm\n'),
                              ('php/development.ini', ''), ('nginx/default.conf', '')]:
            file = template / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(content)
        self.git(template, 'add', '.'); self.git(template, 'commit', '-m', 'Template')
        original = self.git(template, 'rev-parse', 'main')
        ws = work.Workspace(self.root, self.catalog); ws.save()
        env = {'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'repository_identity'), patch.dict(os.environ, env):
            uni_init.docker_branch(ws, 'example-front', 'front', 'example', str(template), 8090, 'example-back', publish=False)
        self.assertEqual(self.git(template, 'rev-parse', 'main'), original)
        bundle = self.root / '.uni/example-front.docker.bundle'
        clone = self.root / 'check'
        self.git(self.root, 'clone', '-b', 'example-front', bundle, clone)
        self.assertIn('http://example-back:80', (clone / 'nginx/default.conf').read_text())
        self.assertIn('external: true', (clone / 'compose.yaml').read_text())
        self.assertTrue((clone / '.env.example').exists())

    def test_full_init_publishes_project_docker_and_catalog(self):
        import uni_init
        cli = self.root / 'cli'; self.repo(cli)
        catalog = cli / 'libs_projects.json'; write_json(catalog, {'libs': {}, 'projects': {}})
        self.git(cli, 'add', '.'); self.git(cli, 'commit', '-m', 'Catalog')
        catalog_remote = self.root / 'catalog.git'; self.git(self.root, 'init', '--bare', catalog_remote)
        self.git(cli, 'remote', 'add', 'origin', catalog_remote); self.git(cli, 'push', '-u', 'origin', 'main')
        project_remote = self.root / 'project.git'; self.git(self.root, 'init', '--bare', project_remote)
        template = self.root / 'docker-template'; self.repo(template)
        for name in ['uni/Dockerfile', 'php/start.sh', 'php/development.ini', 'nginx/default.conf']:
            file = template / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text('# template\n')
        self.git(template, 'add', '.'); self.git(template, 'commit', '-m', 'Template')
        original = self.git(template, 'rev-parse', 'main')
        ws = work.Workspace(self.root, catalog); ws.scan()
        args = SimpleNamespace(catalog=catalog, local=False, vendor='example', project='Demo', name='demo',
                               role='front', environment='demo', port=8098, backend=None, path=None,
                               repository=str(project_remote), docker_repository=str(template),
                               no_install=True, no_framework=True, libs=None)
        env = {'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), patch.object(uni_init, 'repository_identity'), patch.object(projects, 'repository_identity', side_effect=lambda value: value), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        self.assertEqual(self.git(template, 'rev-parse', 'main'), original)
        self.assertIn('PROJECT_PATH', self.git(template, 'show', 'demo:compose.yaml'))
        self.assertEqual(json.loads(self.git(project_remote, 'show', 'main:composer.json'))['name'], 'example/demo')
        published = json.loads(self.git(catalog_remote, 'show', 'main:libs_projects.json'))
        self.assertEqual(published['projects']['demo']['docker']['branch'], 'demo')

    def test_failure_restores_previous_selection_and_env(self):
        self.project('candidate-front'); self.project('other-front')
        ws = work.Workspace(self.root, self.catalog); ws.scan()
        ws.catalog['projects'] = {n: {'docker': {'branch': n}} for n in ['candidate-front', 'other-front']}
        folder = ws.root / 'docker-front'; folder.mkdir()
        (folder / '.env').write_text('original')
        ws.state.update(active='candidate', selected='candidate-front'); ws.save()
        commands = []
        def git(command, cwd=None, capture=True):
            if command[1:3] == ['status', '--porcelain']: return ''
            if command[1:3] == ['branch', '--show-current']: return 'candidate-front'
            return 'abc123'
        def compose(name, args, capture=False):
            commands.append((name, args[0]))
            if name == 'other-front' and args[0] == 'up': raise ValueError('start failed')
        with patch.object(ws, 'prepare'), patch.object(ws, 'write_env', return_value=18080), patch.object(ws, 'compose', side_effect=compose), patch.object(work, 'run', side_effect=git), patch.object(work.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, 'restaurado'): ws.switch('other')
        self.assertEqual(ws.state['active'], 'candidate')
        self.assertEqual((folder / '.env').read_text(), 'original')
        self.assertIn(('candidate-front', 'up'), commands)


if __name__ == '__main__': unittest.main()
