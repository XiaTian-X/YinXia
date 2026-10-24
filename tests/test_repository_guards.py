"""多仓库边界、Git 环境与逐字节只追加规则的回归测试。"""
from __future__ import annotations

import subprocess
import unittest

import test_repositories as fixtures
from helpers import fill_doc


class RepositoryGuardsTest(unittest.TestCase):
    setUp = fixtures.RepositoriesTest.setUp
    cli = fixtures.RepositoriesTest.cli
    git = fixtures.RepositoriesTest.git
    commit = fixtures.RepositoriesTest.commit
    repo = fixtures.RepositoriesTest.repo
    brand = fixtures.RepositoriesTest.brand
    version = fixtures.RepositoriesTest.version

    def test_project_paths_cannot_escape_selected_repository(self):
        self.repo('alpha')
        beta = self.repo('beta')
        brand = self.brand('beta')
        for slug in ('../beta/brand', str(brand)):
            with self.subTest(slug=slug):
                r = self.cli('alpha', 'decision', slug, 'escape', ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('项目短名', r.stderr)
        self.assertEqual(self.git(beta, 'status', '--porcelain').stdout, '')
        self.assertNotEqual(self.cli('alpha', 'new', 'bad\n', ok=False).returncode, 0)
        self.assertFalse((self.ws.root / 'repositories/alpha/bad\n').exists())
        self.assertNotEqual(self.cli('alpha', 'series', 'new', 'bad\n', '--architecture', 'independent', ok=False).returncode, 0)

    def test_symlink_directory_cannot_write_into_another_repository(self):
        alpha = self.repo('alpha')
        beta = self.repo('beta')
        a = self.brand('alpha')
        b = self.brand('beta')
        (a / 'decisions/.gitkeep').unlink()
        (a / 'decisions').rmdir()
        (a / 'decisions').symlink_to(b / 'decisions', target_is_directory=True)
        r = self.cli('alpha', 'decision', 'brand', 'escape', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('符号链接', r.stderr)
        self.assertEqual(self.git(beta, 'status', '--porcelain').stdout, '')
        self.assertTrue((alpha / 'brand/decisions').is_symlink())

    def test_staged_symlink_cannot_hide_behind_regular_worktree_file(self):
        alpha = self.repo('alpha')
        brand = self.brand('alpha')
        brief = brand / 'brief.md'
        old = brief.read_bytes()
        brief.unlink()
        brief.symlink_to(self.ws.root / 'README.md')
        self.git(alpha, 'add', 'brand/brief.md')
        brief.unlink()
        brief.write_bytes(old)
        r = self.git(alpha, 'commit', '-q', '-m', 'reject staged link')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('符号链接', r.stdout + r.stderr)

    def test_git_environment_does_not_override_explicit_repository(self):
        alpha = self.repo('alpha')
        self.repo('beta')
        self.brand('beta')
        env = {**self.ws.env, 'GIT_DIR': str(alpha / '.git'),
               'GIT_WORK_TREE': str(alpha), 'GIT_INDEX_FILE': str(alpha / '.git/index')}
        r = subprocess.run([str(self.ws.root / 'logo'), '--repo', 'beta', 'check'],
                           cwd=alpha, env=env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(alpha, 'status', '--porcelain').stdout, '')
        env['GIT_CONFIG'] = str(self.ws.root / '.git/config')
        r = subprocess.run([str(self.ws.root / 'logo'), '--repo', 'beta', 'setup'],
                           cwd=alpha, env=env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ws.git('system', 'config', 'core.hooksPath').stdout.strip(), '.githooks')
        self.assertEqual(self.git(self.ws.root / 'repositories/beta', 'config', 'core.hooksPath').stdout.strip(), '../../.githooks/projects')

    def test_partial_commit_uses_its_own_index_and_still_protects_frozen_svg(self):
        alpha = self.repo('alpha')
        brand = self.brand('alpha')
        v = self.version('alpha', brand)
        svg = v / 'logo.svg'
        svg.write_text(svg.read_text().replace('90 90', '80 90'))
        self.git(alpha, 'add', '-A')
        notes = v / 'notes.md'
        with notes.open('ab') as f:
            f.write('\n追加评审。\n'.encode())
        r = self.git(alpha, 'commit', '-q', '--only', '-m', 'review only', str(notes.relative_to(alpha)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.git(alpha, 'commit', '-q', '--only', '-m', 'reject rewrite', str(svg.relative_to(alpha)))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('已冻结的文件被修改', r.stdout + r.stderr)

    def test_append_only_preserves_trailing_bytes_in_index_and_history(self):
        alpha = self.repo('alpha')
        brand = self.brand('alpha')
        v = self.version('alpha', brand)
        notes = v / 'notes.md'
        with notes.open('ab') as f:
            f.write(b'\n\n')
        self.commit(alpha, 'append blank lines')
        old = notes.read_bytes()
        notes.write_bytes(old.rstrip(b'\n') + '\n追加说明。\n'.encode())
        self.git(alpha, 'add', '-A')
        notes.write_bytes(old)  # 工作区正常，暂存区仍删掉了原有字节
        r = self.git(alpha, 'commit', '-q', '-m', 'reject truncation')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('只允许在末尾追加', r.stdout + r.stderr)
        self.git(alpha, 'commit', '--no-verify', '-q', '-m', 'history fixture')
        self.git(alpha, 'restore', 'brand/directions/A-mark/v001/notes.md')
        r = self.cli('alpha', 'check')
        self.assertIn('改写了只追加文件', r.stdout)

    def test_series_review_preserves_crlf_and_blank_lines(self):
        alpha = self.repo('alpha')
        self.cli('alpha', 'series', 'new', 'group', '--architecture', 'independent')
        rules = alpha / '_series/group/series.md'
        fill_doc(rules)
        self.cli('alpha', 'new', 'brand', '--series', 'group', '--role', 'master')
        brand = alpha / 'brand'
        fill_doc(brand / 'brief.md')
        self.version('alpha', brand)
        # 先提交附加空行；CRLF 初始版本在独立的新系列中验证。
        self.cli('alpha', 'series', 'new', 'second', '--architecture', 'independent')
        rules = alpha / '_series/second/series.md'
        fill_doc(rules)
        rules.write_bytes(rules.read_bytes().replace(b'\n', b'\r\n') + b'\r\n\r\n')
        self.cli('alpha', 'new', 'other', '--series', 'second', '--role', 'master')
        other = alpha / 'other'
        fill_doc(other / 'brief.md')
        self.version('alpha', other)
        old = rules.read_bytes()
        self.cli('alpha', 'series', 'review', 'second', '--result', 'pass', '--note', '通过')
        self.assertTrue(rules.read_bytes().startswith(old))
        self.commit(alpha, 'append series review')

    def test_system_commit_is_independent_of_invalid_default_links(self):
        self.ws.new_project()
        (self.ws.projects / 'demo/elsewhere').symlink_to(self.ws.root)
        self.repo('alpha')
        self.ws.logo('repo', 'use', 'alpha')
        readme = self.ws.root / 'README.md'
        readme.write_text(readme.read_text() + '\nSystem maintenance.\n')
        r = self.ws.commit('system', 'system only')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('符号链接', r.stdout + r.stderr)

    def test_default_inbox_symlink_cannot_redirect_receive(self):
        from test_handoff import block, GOOD
        self.ws.new_project()
        beta = self.repo('beta')
        self.brand('beta')
        inbox = self.ws.root / 'inbox'
        inbox.mkdir()
        (inbox / 'demo').symlink_to(beta / 'brand/decisions', target_is_directory=True)
        reply = self.ws.write('.cache/reply.txt', block(1, 1, 'badge.svg', GOOD))
        r = self.cli('default', 'handoff', 'receive', str(reply), '--project', 'demo', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('符号链接', r.stderr)
        self.assertEqual(self.git(beta, 'status', '--porcelain').stdout, '')

    def test_outline_destination_respects_repository_and_frozen_files(self):
        alpha = self.repo('alpha')
        beta = self.repo('beta')
        a = self.brand('alpha')
        b = self.brand('beta')
        v = self.version('alpha', a)
        for path, message in ((b / 'new.svg', '另一个设计仓库'), (v / 'logo.svg', '已冻结')):
            with self.subTest(path=path):
                r = self.cli('alpha', 'outline', '--font', 'inter', '--text', 'A', '--fill', '#000000',
                             '--project', 'brand', '--svg', str(path), ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)
        self.assertEqual(self.git(alpha, 'status', '--porcelain').stdout, '')
        self.assertEqual(self.git(beta, 'status', '--porcelain').stdout, '')

    def test_concurrent_registration_keeps_every_repository(self):
        processes = [subprocess.Popen([str(self.ws.root / 'logo'), 'repo', 'new', name],
                                      cwd=self.ws.root, env=self.ws.env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for name in ('alpha', 'beta', 'gamma', 'delta')]
        for process in processes:
            out, err = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, out + err)
        import json
        result = json.loads(self.ws.logo('repo', 'list', '--json').stdout)
        self.assertEqual({r['id'] for r in result['repositories']},
                         {'default', 'alpha', 'beta', 'gamma', 'delta'})

    def test_default_cannot_share_git_storage_with_a_registered_repository(self):
        alpha = self.repo('alpha')
        import shutil
        shutil.rmtree(self.ws.projects / '.git')
        (self.ws.projects / '.git').write_text(f'gitdir: {alpha / ".git"}\n')
        r = self.cli('default', 'status', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('共用 Git', r.stderr)
        self.ws.logo('repo', 'list')  # 管理入口不依赖损坏的 default
        r = self.cli('default', 'check', '--staged', '--scope', 'system')
        self.assertIn('共用 Git', r.stdout)  # 系统维护独立运行，设计仓库异常只提醒
        # 钩子也必须检查 Git 存储归属，不能依赖默认仓库配置。
        r = self.ws.logo('--repo-path', str(self.ws.projects), 'check', '--staged', '--scope', 'projects', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('共用 Git', r.stderr)
        (self.ws.projects / '.git').unlink()
        (self.ws.projects / '.git').symlink_to(alpha / '.git', target_is_directory=True)
        self.assertNotEqual(self.ws.logo('status', ok=False).returncode, 0)
        self.assertNotEqual(self.cli('default', 'status', ok=False).returncode, 0)

    def test_cache_link_and_external_skill_cannot_write_into_design_repository(self):
        import shutil
        alpha = self.repo('alpha')
        self.brand('alpha')
        external = self.ws.tmp / 'external'
        self.git(alpha, 'clone', '--quiet', '--no-local', str(alpha), str(external))
        self.ws.logo('repo', 'add', 'external', str(external))
        r = self.cli('alpha', 'handoff', 'skill', str(external / 'brand'), ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('不能写入设计仓库', r.stderr)
        cache = self.ws.root / '.cache'
        import hashlib
        location = hashlib.sha256(str(alpha.resolve()).encode()).hexdigest()[:16]
        namespace = cache / 'repositories/alpha' / location
        namespace.mkdir(parents=True, exist_ok=True)
        (namespace / 'handoff').symlink_to(external / 'brand', target_is_directory=True)
        r = self.cli('alpha', 'handoff', 'brief', 'brand', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('缓存目录不允许符号链接', r.stderr)
        shutil.rmtree(cache, ignore_errors=True)
        cache.symlink_to(external / 'brand', target_is_directory=True)
        r = self.cli('alpha', 'handoff', 'brief', 'brand', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('缓存目录不允许符号链接', r.stderr)
        self.assertEqual(self.git(external, 'status', '--porcelain').stdout, '')
