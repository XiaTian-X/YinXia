"""封面模块（第二期）：平台登记的拆分、校验与查看。"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import Workspace, logo

import covers  # noqa: E402  helpers 已把 tools/ 加入 sys.path

GOOD = '''
[ratios]
"3x1" = { width = 1500, height = 500 }
[[platform]]
id = "x"
name = "X"
ratio = "3x1"
width = 1500
height = 500
safe = [0, 0, 1500, 500]
occlusions = [[0, 200, 400, 300]]
verified_on = "2099-01-01"
source = "https://example.com"
'''


class PlatformConfigTest(unittest.TestCase):
    def check(self, text: str) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cover-platforms.toml"
            f.write_text(text, encoding="utf-8")
            original, covers.PLATFORMS_TOML = covers.PLATFORMS_TOML, f
            try:
                errors, warnings = [], []
                covers.check_platforms(errors, warnings)
                return errors, warnings
            finally:
                covers.PLATFORMS_TOML = original

    def test_real_config_is_clean(self):
        errors, warnings = [], []
        covers.check_platforms(errors, warnings)
        self.assertEqual((errors, warnings), ([], []))
        cfg = covers.load_config()
        self.assertEqual(len(cfg["platform"]), 15)
        self.assertNotIn("generic", {k for p in cfg["platform"] for k in p})  # 通用比例只在 [ratios]
        self.assertTrue({"4x3", "3x4", "1x1"} <= set(cfg["ratios"]))

    def test_logo_config_has_no_covers(self):
        self.assertFalse((logo.CONFIG / "export-profiles.toml").exists())
        self.assertFalse(hasattr(logo, "load_profiles"))

    def test_valid_minimal(self):
        self.assertEqual(self.check(GOOD), ([], []))

    def test_errors(self):
        cases = {
            "id 重复": GOOD + GOOD[GOOD.index("[[platform]]"):],
            "未在 [ratios] 中定义": GOOD.replace('ratio = "3x1"', 'ratio = "5x1"'),
            "safe": GOOD.replace("safe = [0, 0, 1500, 500]", "safe = [0, 0, 1600, 500]"),
            "occlusions": GOOD.replace("[[0, 200, 400, 300]]", "[[1200, 200, 400, 300]]"),
            "width/height 须为正整数": GOOD.replace("width = 1500\nheight = 500\nsafe", "width = 0\nheight = 500\nsafe"),
            "id 须为小写字母": GOOD.replace('id = "x"', 'id = "X_1"'),
            "缺少 name": GOOD.replace('name = "X"\n', ""),
        }
        for frag, text in cases.items():
            with self.subTest(frag):
                errors, _ = self.check(text)
                self.assertTrue(any(frag in e for e in errors), errors)

    def test_spec_basis_warnings(self):
        _, warnings = self.check(GOOD.replace('source = "https://example.com"', 'basis = "common"'))
        self.assertTrue(any("cover.x" in w and "verify_note" in w for w in warnings), warnings)

    def test_crop_ratio(self):
        self.assertEqual(covers.crop_ratio({"width": 1500, "height": 500}, {"width": 1500, "height": 500}), 0)
        wechat = covers.crop_ratio({"width": 900, "height": 383}, {"width": 1640, "height": 624})
        self.assertAlmostEqual(wechat, 0.106, places=2)  # 2.35:1 从 2.63:1 画布左右共裁约 11%


class CoverCliTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_list(self):
        out = self.ws.logo("cover", "list").stdout
        self.assertEqual(sum(1 for ln in out.splitlines() if "2026-" in ln), 15)
        self.assertIn("wechat-mp", out)
        only = self.ws.logo("cover", "list", "--ratio", "3x1").stdout
        self.assertEqual({ln.split()[0] for ln in only.splitlines() if "2026-" in ln}, {"x", "weibo", "zhihu"})
        self.assertIn("未知画幅", self.ws.logo("cover", "list", "--ratio", "7x2", ok=False).stderr)

    def test_broken_config_blocks_system_commit(self):
        cfg = self.ws.root / "config" / "cover-platforms.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('ratio = "16x9"', 'ratio = "16x10"', 1),
                       encoding="utf-8")
        r = self.ws.commit("system", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(any("16x10" in ln for ln in self.ws.blocked(r)))

    def test_single_logo_module_when_run_as_script(self):
        code = ("import runpy, sys\n"
                "sys.path.insert(0, 'tools')  # 与直接运行脚本一致：脚本所在目录在搜索路径中\n"
                "sys.argv = ['logo', 'cover', 'list']\n"
                "try:\n    runpy.run_path('tools/logo.py', run_name='__main__')\n"
                "except SystemExit:\n    pass\n"
                "print('SAME' if sys.modules['covers'].logo is sys.modules['logo'] "
                "and sys.modules['logo'].__name__ == '__main__' else 'DIFFERENT')\n")
        r = subprocess.run([str(self.ws.root / ".venv" / "bin" / "python"), "-c", code], cwd=self.ws.root,
                           env=self.ws.env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "SAME", r.stderr)


if __name__ == "__main__":
    unittest.main()
