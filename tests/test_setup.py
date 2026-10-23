"""首次安装回归：没有 .venv 的真实启动器，使用离线依赖，不链接现有虚拟环境。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import unittest

from helpers import Workspace


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(setup=False)
        self.addCleanup(self.ws.cleanup)
        (self.ws.root / ".venv").unlink()  # 移除测试夹具链接，复现全新克隆
        root = self.ws.tmp / "workspace with spaces"
        self.ws.root.rename(root)
        self.ws.root = root
        self.bin = self.ws.tmp / "bootstrap-bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        # 使用已安装的依赖供 pip 检测；禁止索引与用户 pip 配置，测试不访问网络。
        self.ws.env.update({
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join(dict.fromkeys(sysconfig.get_path(k) for k in ("purelib", "platlib"))),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        })

    def test_first_setup_creates_environment_and_hooks(self):
        ws = self.ws
        result = subprocess.run([str(ws.root / "logo"), "setup"], cwd=ws.tmp, env=ws.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((ws.root / ".venv").is_symlink())
        self.assertTrue((ws.root / ".venv" / "pyvenv.cfg").is_file())
        self.assertTrue((ws.root / ".venv" / "bin" / "python").exists())
        self.assertTrue((ws.projects / ".git").exists())
        self.assertEqual(ws.git("system", "config", "core.hooksPath").stdout.strip(), ".githooks")
        self.assertEqual(ws.git("projects", "config", "core.hooksPath").stdout.strip(), "../.githooks/projects")
        self.assertIn("还没有项目", ws.logo("status").stdout)
        # 环境已创建后，重复 setup 使用虚拟环境，不再依赖引导解释器。
        (self.bin / "python3").unlink()
        (self.bin / "python3").write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
        (self.bin / "python3").chmod(0o755)
        self.assertIn("环境就绪", ws.logo("setup").stdout)

    def test_other_commands_request_setup_without_writing(self):
        result = self.ws.logo("status", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("请先运行 ./logo setup", result.stderr)
        self.assertFalse((self.ws.root / ".venv").exists())
        self.assertFalse(self.ws.projects.exists())

    def test_missing_or_old_python_has_actionable_error(self):
        (self.bin / "python3").unlink()
        (self.bin / "dirname").symlink_to(shutil.which("dirname"))
        self.ws.env["PATH"] = str(self.bin)
        for old_python in (False, True):
            with self.subTest(old_python=old_python):
                if old_python:
                    (self.bin / "python3").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    (self.bin / "python3").chmod(0o755)
                result = self.ws.logo("setup", ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Python 3.11", result.stderr)
                self.assertFalse((self.ws.root / ".venv").exists())
                self.assertFalse(self.ws.projects.exists())
