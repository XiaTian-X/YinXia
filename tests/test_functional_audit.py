"""常用输入、交接、导出与暂存区边界的功能回归。"""
import json
import tomllib
import unittest

from helpers import HAS_RENDER, PRIMARY, TRIANGLE, Workspace
from test_workflow import prepare_release
from test_handoff import block, GOOD


class FunctionalAuditTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_brand_name_round_trips_quotes_backslashes_and_template_tokens(self):
        name = '印夏 | "Studio" \\ {{date}}\n第二行'
        self.ws.logo('new', 'brand', '--name', name)
        cfg = tomllib.loads((self.ws.projects / 'brand/project.toml').read_text())
        self.assertEqual(cfg['project']['name'], name)
        self.assertIn(name, (self.ws.projects / 'brand/brief.md').read_text())
        self.ws.logo('index')
        board = (self.ws.projects / 'PROJECTS.md').read_text()
        self.assertIn('&#124;', board)
        self.assertIn('<br>第二行', board)
        self.assertEqual(json.loads(self.ws.logo('status', 'brand', '--json').stdout)['projects'][0]['name'], name)

    def test_invalid_release_stage_is_rejected_without_writing_state(self):
        p = self.ws.new_project()
        before = (p / 'project.toml').read_bytes()
        for stage in ('releasing', 'delivered'):
            r = self.ws.logo('state', 'demo', f'stage={stage}', ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('current_release', r.stderr)
            self.assertEqual((p / 'project.toml').read_bytes(), before)

    def test_receive_does_not_overwrite_orphan_sidecar(self):
        self.ws.new_project()
        side = self.ws.write('inbox/demo/badge.svg.import.toml', 'source = "original"\n')
        reply = self.ws.write('.cache/reply.txt', block(1, 2, 'first.svg', GOOD) + block(2, 2, 'badge.svg', GOOD))
        r = self.ws.logo('handoff', 'receive', str(reply), '--project', 'demo', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('badge.svg.import.toml', r.stderr)
        self.assertEqual(side.read_text(), 'source = "original"\n')
        self.assertFalse((side.parent / 'badge.svg').exists())
        self.assertFalse((side.parent / 'first.svg').exists())

    def test_staged_project_config_cannot_hide_behind_clean_worktree(self):
        p = self.ws.new_project()
        cfg = p / 'project.toml'
        original = cfg.read_bytes()
        cfg.write_bytes(original.replace(b'stage = "briefing"', b'stage = "invalid"'))
        self.ws.git('projects', 'add', '-A')
        cfg.write_bytes(original)
        r = self.ws.commit('projects', 'reject invalid state', add=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('stage', r.stdout + r.stderr)

    def test_staged_svg_removed_from_worktree_is_not_skipped(self):
        self.ws.new_project()
        v = self.ws.new_version(svg=TRIANGLE.format(c='#FF0000'))
        self.ws.git('projects', 'add', '-A')
        (v / 'logo.svg').unlink()
        # 保持已有方向，但把新增版本整体移出工作区来绕过目录遍历。
        v.rename(self.ws.tmp / 'hidden-version')
        self.ws.logo('index')
        r = self.ws.commit('projects', 'reject hidden svg', add=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('logo.svg', r.stdout + r.stderr)

    @unittest.skipUnless(HAS_RENDER, '需要 resvg 与 magick')
    def test_duplicate_export_targets_keep_complete_delivery(self):
        p = self.ws.new_project()
        self.ws.new_version()
        self.assertEqual(self.ws.commit('projects', 'version').returncode, 0)
        prepare_release(self.ws)
        self.ws.logo('export', 'demo', 'svg')
        before = {f.name: f.read_bytes() for f in (p / 'dist/r1/svg').iterdir()}
        self.ws.logo('export', 'demo', 'svg', 'svg')
        self.assertEqual(before, {f.name: f.read_bytes() for f in (p / 'dist/r1/svg').iterdir()})
        self.assertEqual(set(json.loads((p / 'dist/r1/manifest.json').read_text())['targets']), {'svg'})

    @unittest.skipUnless(HAS_RENDER, '需要 resvg 与 magick')
    def test_palette_change_requires_new_import_preview(self):
        p = self.ws.new_project()
        self.ws.new_version()
        self.assertEqual(self.ws.commit('projects', 'version').returncode, 0)
        source = self.ws.write('.cache/source.svg', TRIANGLE.format(c='#FF0000'))
        args = ('import', str(source), '--project', 'demo', '--direction', 'A', '--resolve', '#FF0000=map:primary')
        self.ws.logo(*args, '--analyze')
        cfg = p / 'project.toml'
        cfg.write_text(cfg.read_text().replace(f'primary = "{PRIMARY}"', 'primary = "#445566"', 1))
        r = self.ws.logo(*args, '--source', 'test', '--terms', 'unknown', '--kind', 'candidate', '--note', '用户确认', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('请先用同样的文件', r.stderr)
        self.assertFalse((p / 'directions/A-mountain/v002').exists())
        self.ws.logo(*args, '--analyze')
        self.ws.logo(*args, '--source', 'test', '--terms', 'unknown', '--kind', 'candidate', '--note', '用户重新确认')
        self.assertTrue((p / 'directions/A-mountain/v002/logo.svg').exists())

    def test_staged_board_cannot_hide_behind_current_worktree(self):
        self.ws.new_project()
        board = self.ws.projects / 'PROJECTS.md'
        original = board.read_bytes()
        board.write_text('# Outdated board\n')
        self.ws.git('projects', 'add', '-A')
        board.write_bytes(original)
        r = self.ws.commit('projects', 'reject outdated board', add=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('PROJECTS.md 不是最新', r.stdout + r.stderr)

    @unittest.skipUnless(HAS_RENDER, '需要 resvg 与 magick')
    def test_partial_export_does_not_reaffirm_modified_old_outputs(self):
        p = self.ws.new_project()
        self.ws.new_version()
        self.assertEqual(self.ws.commit('projects', 'version').returncode, 0)
        prepare_release(self.ws)
        self.ws.logo('export', 'demo', 'svg', 'ios')
        exported = p / 'dist/r1/svg/demo-primary-color.svg'
        changed = exported.read_bytes() + b'\n<!-- edited after export -->\n'
        exported.write_bytes(changed)
        self.ws.logo('export', 'demo', 'ios')
        manifest = json.loads((p / 'dist/r1/manifest.json').read_text())
        self.assertEqual(exported.read_bytes(), changed)
        self.assertTrue(all(value is None for value in manifest['targets']['svg'].values()))
        self.assertIsNotNone(manifest['targets']['ios']['git_head'])
