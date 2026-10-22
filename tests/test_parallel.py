"""并行运行器与工作区模板：分组（共享夹具整类）、最长任务优先分配、结果核对、模板隔离。"""
from __future__ import annotations

import unittest

import parallel
from helpers import Workspace


class WithFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pass

    def test_a(self):
        pass

    def test_b(self):
        pass


class Plain(unittest.TestCase):
    def test_c(self):
        pass

    def test_d(self):
        pass


class RunnerLogicTest(unittest.TestCase):
    def tests(self):
        loader = unittest.defaultTestLoader
        return parallel.flatten(loader.loadTestsFromTestCase(WithFixture)) + parallel.flatten(loader.loadTestsFromTestCase(Plain))

    def test_units_keep_shared_fixtures_together(self):
        units = parallel.make_units(self.tests())
        self.assertIn([f"{__name__}.WithFixture.test_a", f"{__name__}.WithFixture.test_b"], units)
        self.assertIn([f"{__name__}.Plain.test_c"], units)  # 无共享夹具：逐个测试分配
        self.assertIn([f"{__name__}.Plain.test_d"], units)
        self.assertTrue(parallel.has_shared_fixture(WithFixture("test_a")))
        self.assertFalse(parallel.has_shared_fixture(Plain("test_c")))

    def test_longest_first_balances_load(self):
        units = [[f"t{i}"] for i in range(6)]
        times = {"t0": 10, "t1": 9, "t2": 8, "t3": 1, "t4": 1, "t5": 1}
        buckets = parallel.plan(units, times, 2)
        loads = sorted(sum(times[i] for i in b) for b in buckets)
        self.assertEqual(loads, [13, 17])  # {10,1,1,1} 与 {9,8}：这组数据能分出的最均衡结果
        self.assertEqual(sorted(i for b in buckets for i in b), [f"t{i}" for i in range(6)])  # 不漏不重
        self.assertEqual(len(parallel.plan(units, {}, 8)), 6)  # 进程数不超过单元数；无记录时按默认估算

    def test_account_detects_missing_and_fixture_errors(self):
        expected = ["m.A.test_1", "m.A.test_2", "m.B.test_3"]
        records = [{"id": "m.B.test_3", "fixture": False}]
        self.assertEqual(parallel.account(expected, records), ["m.A.test_1", "m.A.test_2"])  # 漏跑被发现
        records.append({"id": "setUpClass (m.A)", "fixture": True})
        self.assertEqual(parallel.account(expected, records), [])  # 夹具出错：错误已记在夹具上


class TemplateIsolationTest(unittest.TestCase):
    def test_copied_workspaces_are_independent(self):
        a, b = Workspace(), Workspace()
        self.addCleanup(a.cleanup)
        self.addCleanup(b.cleanup)
        b.logo("new", "demo")
        b.write("tools/extra.txt", "x")
        self.assertFalse((a.projects / "demo").exists())
        self.assertFalse((a.root / "tools" / "extra.txt").exists())
        c = Workspace()  # 之后复制出来的工作区仍是干净的模板
        self.addCleanup(c.cleanup)
        self.assertFalse((c.projects / "demo").exists())
        self.assertEqual(c.git("projects", "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(c.git("projects", "config", "core.hooksPath").stdout.strip(), "../.githooks/projects")


if __name__ == "__main__":
    unittest.main()
