import json
import os
import re
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
        folder = self.root / (relative or name)
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

    def test_editor_eof_keeps_selected_environment(self):
        ws = work.Workspace(self.root, self.catalog)
        with patch('builtins.input', side_effect=EOFError), patch.object(work, 'sys_stdin_tty', return_value=True), patch.object(work.subprocess, 'run') as run:
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
        write_json(catalog, {'libs': {}, 'projects': {'test': {
            'repository': 'https://example.com/test.git', 'composer_name': 'example/test', 'root_name': 'test',
            'docker': {'repository': 'https://example.com/docker.git', 'branch': 'test'}}}})
        (repo / 'tool.py').write_text('unrelated change')
        projects.publish_catalog(catalog)
        published = json.loads(self.git(remote, 'show', 'main:libs_projects.json'))
        self.assertIn('test', published['projects'])
        self.assertEqual(self.git(remote, 'show', 'main:tool.py'), 'original')
        self.assertIn('tool.py', self.git(repo, 'status', '--short'))
        with patch('builtins.print') as printed:
            projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=True), uni.load_config)
        printed.assert_any_call('Exclusao local; branch Docker preservada em https://example.com/docker.git. Publicacao pendente (uni catalog publish).')
        projects.sync_catalog(catalog)
        self.assertNotIn('test', json.loads(catalog.read_text())['projects'])
        self.git(repo, 'commit', '--allow-empty', '-m', 'Pending local commit')
        projects.publish_catalog(catalog)
        self.assertNotIn('test', json.loads(self.git(remote, 'show', 'main:libs_projects.json'))['projects'])

    def test_delete_when_remote_already_lacks_locally_changed_project(self):
        remote = self.root / 'remote.git'; self.git(self.root, 'init', '--bare', remote)
        repo = self.root / 'cli'; self.repo(repo)
        catalog = repo / 'libs_projects.json'; write_json(catalog, {'libs': {}, 'projects': {}})
        self.git(repo, 'add', '.'); self.git(repo, 'commit', '-m', 'Initial')
        self.git(repo, 'remote', 'add', 'origin', remote); self.git(repo, 'push', '-u', 'origin', 'main')
        entry = {'repository': 'https://example.com/test.git', 'composer_name': 'example/old', 'root_name': 'test'}
        write_json(catalog, {'libs': {}, 'projects': {'test': entry}})
        self.git(repo, 'add', '.'); self.git(repo, 'commit', '-m', 'Local registration')
        entry['composer_name'] = 'example/new'
        write_json(catalog, {'libs': {}, 'projects': {'test': entry}})
        before = catalog.read_bytes()
        with patch.object(projects, 'sync_catalog', side_effect=ValueError('fetch failed')):
            with self.assertRaisesRegex(ValueError, 'fetch failed'):
                projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=False), uni.load_config)
        self.assertEqual(catalog.read_bytes(), before)
        projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=False), uni.load_config)
        self.assertNotIn('test', json.loads(catalog.read_text())['projects'])
        self.assertNotIn('test', json.loads(self.git(remote, 'show', 'main:libs_projects.json'))['projects'])

    def test_delete_rejects_registered_backend_dependency(self):
        write_json(self.catalog, {'libs': {}, 'projects': {
            'api': {'repository': 'https://example.com/api.git', 'composer_name': 'example/api', 'root_name': 'api'},
            'front': {'repository': 'https://example.com/front.git', 'composer_name': 'example/front',
                      'root_name': 'front', 'related': {'backend': 'api'}}}})
        args = SimpleNamespace(catalog=self.catalog, name='api', local=True)
        before = self.catalog.read_bytes()
        with self.assertRaisesRegex(ValueError, 'front'):
            projects.delete_and_publish(args, uni.load_config)
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_delete_removes_docker_branch_and_recovers_orphan(self):
        remote = self.root / 'catalog.git'; self.git(self.root, 'init', '--bare', remote)
        cli = self.root / 'cli'; self.repo(cli)
        catalog = cli / 'libs_projects.json'
        docker_remote = self.root / 'docker.git'; self.git(self.root, 'init', '--bare', docker_remote)
        default_remote = self.root / 'default.git'; self.git(self.root, 'init', '--bare', default_remote)
        docker = self.root / 'docker'; self.repo(docker)
        (docker / 'README').write_text('template')
        self.git(docker, 'add', '.'); self.git(docker, 'commit', '-m', 'Template')
        self.git(docker, 'remote', 'add', 'origin', docker_remote); self.git(docker, 'push', '-u', 'origin', 'main')
        self.git(docker, 'push', 'origin', 'main:test')
        self.git(docker, 'remote', 'add', 'default', default_remote); self.git(docker, 'push', 'default', 'main')
        self.git(docker, 'push', 'default', 'main:test')
        entry = {'repository': 'https://example.com/test.git', 'composer_name': 'example/test', 'root_name': 'test',
                 'docker': {'repository': str(docker_remote), 'branch': 'test'}}
        write_json(catalog, {'libs': {}, 'projects': {'test': entry}, 'docker_repository': str(default_remote)})
        self.git(cli, 'add', '.'); self.git(cli, 'commit', '-m', 'Catalog')
        self.git(cli, 'remote', 'add', 'origin', remote); self.git(cli, 'push', '-u', 'origin', 'main')
        write_json(catalog, {'libs': {}, 'projects': {}, 'docker_repository': str(default_remote)})
        args = SimpleNamespace(catalog=catalog, name='test', local=False)
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            projects.delete_and_publish(args, uni.load_config)
            self.assertEqual(self.git(docker, 'ls-remote', 'origin', 'refs/heads/test'), '')
            self.assertNotEqual(self.git(docker, 'ls-remote', 'default', 'refs/heads/test'), '')
            self.assertNotIn('test', json.loads(self.git(remote, 'show', 'main:libs_projects.json'))['projects'])
            write_json(catalog, {'libs': {}, 'projects': {'test': entry}, 'docker_repository': str(default_remote)})
            self.git(cli, 'add', 'libs_projects.json'); self.git(cli, 'commit', '-m', 'Restore catalog')
            self.git(cli, 'push')
            projects.delete_and_publish(args, uni.load_config)
            self.git(docker, 'push', 'origin', 'main:test')
            with self.assertRaisesRegex(ValueError, 'informe --docker-repository'):
                projects.delete_and_publish(args, uni.load_config)
            projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=False,
                                                        docker_repository=str(docker_remote)), uni.load_config)
            self.assertEqual(self.git(docker, 'ls-remote', 'origin', 'refs/heads/test'), '')
            self.assertNotEqual(self.git(docker, 'ls-remote', 'default', 'refs/heads/test'), '')
            with self.assertRaisesRegex(ValueError, 'nao existe'):
                projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=False,
                                                            docker_repository=str(docker_remote)), uni.load_config)
            write_json(catalog, {'libs': {}, 'projects': {'test': {**entry, 'docker': {'repository': str(docker_remote), 'branch': 'main'}}}})
            with self.assertRaisesRegex(ValueError, 'main'):
                projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=True), uni.load_config)

    def test_delete_protects_configured_docker_model(self):
        write_json(self.catalog, {'libs': {}, 'projects': {'test': {
            'repository': 'https://example.invalid/test.git',
            'composer_name': 'example/test',
            'root_name': 'test',
            'docker': {'repository': 'https://example.invalid/docker.git', 'branch': 'padrao'}
        }}, 'docker_base_branch': 'padrao'})
        args = SimpleNamespace(catalog=self.catalog, name='test', local=True)
        with patch.object(projects, 'repository_identity'):
            with self.assertRaisesRegex(ValueError, 'padrao'):
                projects.delete_and_publish(args, uni.load_config)

    def test_delete_branch_failure_is_retryable(self):
        remote = self.root / 'catalog.git'; self.git(self.root, 'init', '--bare', remote)
        cli = self.root / 'cli'; self.repo(cli)
        catalog = cli / 'libs_projects.json'
        docker_remote = self.root / 'docker.git'; self.git(self.root, 'init', '--bare', docker_remote)
        docker = self.root / 'docker'; self.repo(docker)
        (docker / 'README').write_text('template')
        self.git(docker, 'add', '.'); self.git(docker, 'commit', '-m', 'Template')
        self.git(docker, 'remote', 'add', 'origin', docker_remote); self.git(docker, 'push', '-u', 'origin', 'main')
        self.git(docker, 'push', 'origin', 'main:test')
        entry = {'repository': 'https://example.com/test.git', 'composer_name': 'example/test', 'root_name': 'test',
                 'docker': {'repository': str(docker_remote), 'branch': 'test'}}
        write_json(catalog, {'libs': {}, 'projects': {'test': entry}, 'docker_repository': str(docker_remote)})
        self.git(cli, 'add', '.'); self.git(cli, 'commit', '-m', 'Catalog')
        self.git(cli, 'remote', 'add', 'origin', remote); self.git(cli, 'push', '-u', 'origin', 'main')
        original_run = work.run
        def fail_delete(command, *args, **kwargs):
            if list(map(str, command)) == ['git', 'push', str(docker_remote), '--delete', 'test']:
                raise ValueError('remote rejected')
            return original_run(command, *args, **kwargs)
        args = SimpleNamespace(catalog=catalog, name='test', local=False)
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value), patch.object(work, 'run', side_effect=fail_delete):
            with self.assertRaisesRegex(ValueError, re.escape(str(docker_remote))):
                projects.delete_and_publish(args, uni.load_config)
        self.assertNotIn('test', json.loads(self.git(remote, 'show', 'main:libs_projects.json'))['projects'])
        self.assertNotEqual(self.git(docker, 'ls-remote', 'origin', 'refs/heads/test'), '')
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            projects.delete_and_publish(SimpleNamespace(catalog=catalog, name='test', local=False,
                                                        docker_repository=str(docker_remote)), uni.load_config)
        self.assertEqual(self.git(docker, 'ls-remote', 'origin', 'refs/heads/test'), '')

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
                               no_install=True, no_framework=True, libs=None, no_link=True)
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        root = self.root / 'new-project'
        self.assertTrue((root / 'public/index.php').is_file())
        self.assertEqual(json.loads((root / 'composer.json').read_text())['autoload']['psr-4'], {'NewProject\\': 'src/'})
        self.assertNotIn('ApplicationCore', (root / 'public/index.php').read_text())
        self.assertFalse(json.loads((root / 'composer.json').read_text())['extra']['uni']['related']['link_requested'])
        self.assertIn('new-project', ws.state['projects'])
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws):
            with self.assertRaisesRegex(ValueError, 'cadastrado'):
                uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)

    def test_init_accepts_hyphenated_project_name(self):
        root = self.root / 'new-project'; root.mkdir()
        self.assertEqual(uni.init_project('example', 'New-Project', root), 'example/new-project')
        manifest = json.loads((root / 'composer.json').read_text())
        self.assertEqual(manifest['autoload']['psr-4'], {'NewProject\\': 'src/'})

    def test_docker_branch_uses_configured_base_without_changing_it(self):
        import uni_init
        template = self.root / 'template'; self.repo(template)
        for name, content in [('uni/Dockerfile', 'FROM php:8.5-cli-alpine\n'),
                              ('php/start.sh', '#!/bin/sh\nexec php-fpm\n'),
                              ('php/development.ini', ''), ('nginx/default.conf', '')]:
            file = template / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(content)
        self.git(template, 'add', '.'); self.git(template, 'commit', '-m', 'Template')
        original = self.git(template, 'rev-parse', 'main')
        self.git(template, 'switch', '-c', 'padrao')
        (template / 'base-marker').write_text('padrao')
        self.git(template, 'add', 'base-marker'); self.git(template, 'commit', '-m', 'Standard model')
        model = self.git(template, 'rev-parse', 'padrao')
        self.git(template, 'switch', 'main')
        write_json(self.catalog, {'libs': {}, 'projects': {}, 'docker_base_branch': 'padrao'})
        ws = work.Workspace(self.root, self.catalog); ws.save()
        env = {'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'repository_identity'), patch.dict(os.environ, env):
            uni_init.docker_branch(ws, 'example-front', 'front', 'example', str(template), 8090, 'example-back', publish=False)
        self.assertEqual(self.git(template, 'rev-parse', 'main'), original)
        self.assertEqual(self.git(template, 'rev-parse', 'padrao'), model)
        bundle = self.root / '.uni/example-front.docker.bundle'
        clone = self.root / 'check'
        self.git(self.root, 'clone', '-b', 'example-front', bundle, clone)
        self.assertEqual((clone / 'base-marker').read_text(), 'padrao')
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
                               no_install=True, no_framework=True, libs=None, no_link=True)
        env = {'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), patch.object(uni_init, 'repository_identity'), patch.object(projects, 'repository_identity', side_effect=lambda value: value), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        self.assertEqual(self.git(template, 'rev-parse', 'main'), original)
        self.assertIn('PROJECT_PATH', self.git(template, 'show', 'demo:compose.yaml'))
        self.assertEqual(json.loads(self.git(project_remote, 'show', 'main:composer.json'))['name'], 'example/demo')
        published = json.loads(self.git(catalog_remote, 'show', 'main:libs_projects.json'))
        self.assertEqual(published['projects']['demo']['docker']['branch'], 'demo')

    def pair_workspace(self):
        import uni_init
        cli = self.root / 'cli'; self.repo(cli)
        catalog = cli / 'libs_projects.json'
        write_json(catalog, {'libs': {}, 'projects': {}})
        self.git(cli, 'add', '.'); self.git(cli, 'commit', '-m', 'Catalog')
        catalog_remote = self.root / 'catalog.git'; self.git(self.root, 'init', '--bare', catalog_remote)
        self.git(cli, 'remote', 'add', 'origin', catalog_remote); self.git(cli, 'push', '-u', 'origin', 'main')
        docker = self.root / 'docker-template'; self.repo(docker)
        for name in ['uni/Dockerfile', 'php/start.sh', 'php/development.ini', 'nginx/default.conf']:
            file = docker / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text('# template\n')
        self.git(docker, 'add', '.'); self.git(docker, 'commit', '-m', 'Template')
        ws = work.Workspace(self.root, catalog); ws.scan()
        env = {'GIT_AUTHOR_NAME': 'CLI Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
               'GIT_COMMITTER_NAME': 'CLI Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        return uni_init, ws, catalog, docker, env

    def create_pair_member(self, role, requested, fixture, backend=None):
        uni_init, ws, catalog, docker, env = fixture
        name = 'demo-' + role
        remote = self.root / (name + '.git'); self.git(self.root, 'init', '--bare', remote)
        args = SimpleNamespace(catalog=catalog, local=False, vendor='example', project='Demo' + role.title(),
                               name=name, role=role, environment='demo', port=8080 if role == 'front' else 8082,
                               backend=backend, path=None, repository=str(remote), docker_repository=str(docker),
                               no_install=True, no_framework=True, libs=None, link=requested,
                               no_link=not requested)
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), \
                patch.object(uni_init, 'repository_identity'), \
                patch.object(projects, 'repository_identity', side_effect=lambda value: value), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        return name

    def assert_pair_linked(self, order):
        fixture = self.pair_workspace()
        for role in order:
            self.create_pair_member(role, True, fixture)
        _, _, catalog, docker, _ = fixture
        data = uni.load_config(catalog)
        self.assertEqual(data['projects']['demo-front']['related']['backend'], 'demo-back')
        self.assertIn('http://demo-back:80', self.git(docker, 'show', 'demo-front:nginx/default.conf'))

    def test_pair_links_backend_first(self):
        self.assert_pair_linked(('back', 'front'))

    def test_pair_links_frontend_first(self):
        self.assert_pair_linked(('front', 'back'))

    def test_independent_front_stays_unlinked_until_explicit_link(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        self.assertEqual(fixture[1].members('demo'), ['demo-front'])
        self.create_pair_member('back', True, fixture)
        _, _, catalog, docker, _ = fixture
        self.assertNotIn('backend', uni.load_config(catalog)['projects']['demo-front']['related'])
        self.assertNotIn('proxy_pass $backend', self.git(docker, 'show', 'demo-front:nginx/default.conf'))
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
            projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertEqual(uni.load_config(catalog)['projects']['demo-front']['related']['backend'], 'demo-back')
        self.assertIn('http://demo-back:80', self.git(docker, 'show', 'demo-front:nginx/default.conf'))

    def test_interactive_back_can_override_independent_front(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        with patch.object(projects.sys, 'stdin', SimpleNamespace(isatty=lambda: True)), \
                patch('builtins.input', return_value='s') as answer:
            self.create_pair_member('back', True, fixture)
        answer.assert_called_once()
        catalog, docker = fixture[2], fixture[3]
        self.assertEqual(uni.load_config(catalog)['projects']['demo-front']['related']['backend'], 'demo-back')
        self.assertIn('http://demo-back:80', self.git(docker, 'show', 'demo-front:nginx/default.conf'))

    def test_interactive_back_respects_refusal(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        with patch.object(projects.sys, 'stdin', SimpleNamespace(isatty=lambda: True)), \
                patch('builtins.input', return_value='n') as answer:
            self.create_pair_member('back', True, fixture)
        answer.assert_called_once()
        catalog, docker = fixture[2], fixture[3]
        self.assertNotIn('backend', uni.load_config(catalog)['projects']['demo-front']['related'])
        self.assertNotIn('proxy_pass $backend', self.git(docker, 'show', 'demo-front:nginx/default.conf'))

    def test_backend_flag_keeps_legacy_explicit_link(self):
        fixture = self.pair_workspace()
        self.create_pair_member('back', False, fixture)
        self.create_pair_member('front', True, fixture, backend='demo-back')
        catalog, docker = fixture[2], fixture[3]
        self.assertEqual(uni.load_config(catalog)['projects']['demo-front']['related']['backend'], 'demo-back')
        self.assertIn('http://demo-back:80', self.git(docker, 'show', 'demo-front:nginx/default.conf'))

    def test_link_rejects_customized_generated_files(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        self.create_pair_member('back', False, fixture)
        catalog, docker = fixture[2], fixture[3]
        self.git(docker, 'switch', 'demo-front')
        with (docker / 'compose.yaml').open('a') as output:
            output.write('\n# customizado\n')
        self.git(docker, 'add', 'compose.yaml'); self.git(docker, 'commit', '-m', 'Customize')
        self.git(docker, 'switch', 'main')
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            with self.assertRaisesRegex(ValueError, 'personalizados'):
                projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertNotIn('backend', uni.load_config(catalog)['projects']['demo-front']['related'])

    def test_link_push_failure_is_retryable(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        self.create_pair_member('back', False, fixture)
        catalog, docker = fixture[2], fixture[3]
        original_run = work.run
        def reject_push(command, *args, **kwargs):
            if command[:3] == ['git', 'push', 'origin'] and command[-1] == 'HEAD:demo-front':
                raise ValueError('push rejected')
            return original_run(command, *args, **kwargs)
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value), \
                patch.object(work, 'run', side_effect=reject_push):
            with self.assertRaisesRegex(ValueError, 'repita: uni project link'):
                projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertNotIn('backend', uni.load_config(catalog)['projects']['demo-front']['related'])
        self.assertNotIn('proxy_pass $backend', self.git(docker, 'show', 'demo-front:nginx/default.conf'))
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertEqual(uni.load_config(catalog)['projects']['demo-front']['related']['backend'], 'demo-back')

    def test_link_catalog_failure_restores_relation_and_retries(self):
        fixture = self.pair_workspace()
        self.create_pair_member('front', False, fixture)
        self.create_pair_member('back', False, fixture)
        catalog, docker = fixture[2], fixture[3]
        original_run = work.run
        def reject_catalog_push(command, *args, **kwargs):
            if command[:3] == ['git', 'push', 'origin'] and command[-1] == 'HEAD:main':
                raise ValueError('catalog push rejected')
            return original_run(command, *args, **kwargs)
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value), \
                patch.object(work, 'run', side_effect=reject_catalog_push):
            with self.assertRaisesRegex(ValueError, 'catalogo local restaurado'):
                projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertNotIn('backend', uni.load_config(catalog)['projects']['demo-front']['related'])
        self.assertEqual(json.loads(self.git(catalog.parent, 'show', 'HEAD:libs_projects.json'))
                         ['projects']['demo-front']['related']['backend'], 'demo-back')
        self.assertNotIn('backend', json.loads(self.git(self.root / 'catalog.git', 'show', 'main:libs_projects.json'))
                         ['projects']['demo-front']['related'])
        self.assertIn('http://demo-back:80', self.git(docker, 'show', 'demo-front:nginx/default.conf'))
        with patch.object(projects, 'repository_identity', side_effect=lambda value: value):
            projects.link_projects('demo-front', 'demo-back', catalog, uni.load_config)
        self.assertEqual(uni.load_config(catalog)['projects']['demo-front']['related']['backend'], 'demo-back')

    def test_link_choice_requires_flags_without_tty(self):
        args = SimpleNamespace(backend=None, link=False, no_link=False)
        with patch.object(projects.sys, 'stdin', SimpleNamespace(isatty=lambda: False)):
            with self.assertRaisesRegex(ValueError, '--link ou --no-link'):
                projects.choose_link(args, 'front')
        self.assertTrue(projects.choose_link(SimpleNamespace(backend='demo-back', link=False, no_link=False), 'front'))
        with self.assertRaisesRegex(ValueError, '--backend e --no-link'):
            projects.choose_link(SimpleNamespace(backend='demo-back', link=False, no_link=True), 'front')

    def test_register_preserves_existing_role_and_local_intention(self):
        folder = self.project('service')
        data = {'libs': {}, 'projects': {'service': {
            'repository': 'https://example.com/service.git', 'composer_name': 'example/service',
            'root_name': 'service', 'role': 'back', 'environment': 'demo'}}}
        write_json(self.catalog, data)
        args = SimpleNamespace(catalog=self.catalog, local=True, repository='https://example.com/service.git',
                               path=folder, name='service', role=None, environment='demo', backend=None,
                               docker_repository=None, link=False, no_link=True)
        with patch.object(projects, 'register_project', return_value='service'), \
                patch.object(work, 'Workspace', side_effect=ValueError('mapa indisponivel')):
            projects.register_and_publish(args, uni.load_config)
        self.assertEqual(uni.load_config(self.catalog)['projects']['service']['role'], 'back')
        manifest = json.loads((folder / 'composer.json').read_text())
        self.assertFalse(manifest['extra']['uni']['related']['link_requested'])

    def test_local_init_records_intention_without_remote_link(self):
        import uni_init
        fixture = self.pair_workspace()
        _, ws, catalog, docker, env = fixture
        remote = self.root / 'local-project.git'; self.git(self.root, 'init', '--bare', remote)
        args = SimpleNamespace(catalog=catalog, local=True, vendor='example', project='LocalFront',
                               name='local-front', role='front', environment='local', port=8090,
                               backend=None, path=None, repository=str(remote), docker_repository=str(docker),
                               no_install=True, no_framework=True, libs=None, link=True, no_link=False)
        with patch.object(uni_init, 'doctor'), patch.object(uni_init, 'Workspace', return_value=ws), \
                patch.object(uni_init, 'repository_identity'), \
                patch.object(projects, 'repository_identity', side_effect=lambda value: value), patch.dict(os.environ, env):
            uni_init.initialize(args, uni.init_project, uni.select_libraries, uni.load_config)
        entry = uni.load_config(catalog)['projects']['local-front']
        self.assertTrue(entry['related']['link_requested'])
        self.assertNotIn('backend', entry['related'])
        self.assertNotIn('docker', entry)
        self.assertEqual(self.git(self.root, 'ls-remote', remote), '')
        manifest = json.loads((self.root / 'local-front/composer.json').read_text())
        self.assertTrue(manifest['extra']['uni']['related']['link_requested'])

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
