"""并行运行 tests/ 下的测试（./logo selftest 调用；文件名不以 test 开头，不会被当作测试收集）。

- 分组：用了 setUpClass / setUpModule 共享夹具的测试类整类为一个单元（夹具只建一次），其余测试各自为单元；
- 分配：按上次各测试的耗时（.cache/selftest-times.json）用“最长任务优先”分给 N 个工作进程；
- 汇总：合并各进程结果，列出失败详情；测试数须与收集到的一致，工作进程异常退出时其未完成的测试记为错误。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMES = HERE.parent / ".cache" / "selftest-times.json"
DEFAULT_SECONDS = 3.0
DEFAULT_TIMEOUT = 1800  # 总超时（秒）：某个测试卡住时不至于一直挂着


# ---------------------------------------------------------------------------
# 收集与分组
# ---------------------------------------------------------------------------
def flatten(suite) -> list[unittest.TestCase]:
    out = []
    for item in suite:
        out.extend(flatten(item) if isinstance(item, unittest.TestSuite) else [item])
    return out


def class_key(test: unittest.TestCase) -> str:
    return f"{type(test).__module__}.{type(test).__qualname__}"


def has_shared_fixture(test: unittest.TestCase) -> bool:
    """测试类（或其模块）是否有共享夹具：有则整类须在同一进程内连续运行。"""
    cls = type(test)
    custom = any(getattr(cls, n).__func__ is not getattr(unittest.TestCase, n).__func__
                 for n in ("setUpClass", "tearDownClass"))
    module = sys.modules.get(cls.__module__)
    return custom or bool(module and (hasattr(module, "setUpModule") or hasattr(module, "tearDownModule")))


def is_load_failure(test: unittest.TestCase) -> bool:
    return type(test).__name__ in ("_FailedTest", "ModuleImportFailure")


def make_units(tests: list[unittest.TestCase]) -> list[list[str]]:
    """按收集顺序分组：共享夹具的类整类一个单元，其余每个测试一个单元。"""
    units: dict[str, list[str]] = {}
    for t in tests:
        key = class_key(t) if has_shared_fixture(t) else t.id()
        units.setdefault(key, []).append(t.id())
    return list(units.values())


def plan(units: list[list[str]], times: dict[str, float], jobs: int) -> list[list[str]]:
    """最长任务优先：每次把最耗时的单元分给当前总耗时最少的进程。"""
    known = [v for v in times.values() if v > 0]
    default = statistics.median(known) if known else DEFAULT_SECONDS
    cost = lambda u: sum(times.get(i, default) for i in u)  # noqa: E731
    buckets: list[tuple[float, list[str]]] = [(0.0, []) for _ in range(max(1, min(jobs, len(units))))]
    for u in sorted(units, key=cost, reverse=True):
        i = min(range(len(buckets)), key=lambda k: buckets[k][0])
        load, ids = buckets[i]
        buckets[i] = (load + cost(u), ids + u)
    return [ids for _, ids in buckets if ids]


# ---------------------------------------------------------------------------
# 工作进程
# ---------------------------------------------------------------------------
class RecordingResult(unittest.TextTestResult):
    """记录每个测试的结果与耗时（含 setUpClass 等夹具错误）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []
        self._start = 0.0

    def startTest(self, test):
        self._start = time.time()
        super().startTest(test)

    def _record(self, test, status: str, detail: str = "") -> None:
        fixture = not isinstance(test, unittest.TestCase) or type(test).__name__ == "_ErrorHolder"
        self.records.append({"id": test.id() if not fixture else str(test), "status": status, "detail": detail,
                             "seconds": round(time.time() - self._start, 3) if not fixture else 0.0,
                             "fixture": fixture, "class": getattr(test, "description", "") if fixture else ""})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skip", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "FAIL", "unexpected success")


def worker(ids_file: str, out_file: str) -> None:
    ids = json.loads(Path(ids_file).read_text(encoding="utf-8"))
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(i) for i in ids)
    runner = unittest.TextTestRunner(resultclass=RecordingResult, verbosity=0, stream=sys.stderr)
    result = runner.run(suite)
    tmp = Path(out_file + ".tmp")
    tmp.write_text(json.dumps(result.records, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_file)  # 写完才出现：没有结果文件即视为工作进程异常退出


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def account(expected: list[str], records: list[dict]) -> list[str]:
    """核对：收集到的测试都须有结果；共享夹具出错的类，其测试视为已报告（错误已记在夹具上）。"""
    done = {r["id"] for r in records if not r["fixture"]}
    broken = " ".join(r["id"] for r in records if r["fixture"])
    return [i for i in expected if i not in done and f"({i.rsplit('.', 1)[0]})" not in broken]


def main() -> int:
    ap = argparse.ArgumentParser(prog="parallel")
    ap.add_argument("-j", "--jobs", type=int, default=min(os.cpu_count() or 1, 8))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="总超时秒数")
    ap.add_argument("--worker", nargs=2, metavar=("IDS", "OUT"))
    ap.add_argument("tests", nargs="*")
    a = ap.parse_args()
    if a.worker:
        worker(*a.worker)
        return 0
    started = time.time()
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromNames(a.tests) if a.tests else loader.discover(str(HERE))
    tests = flatten(suite)
    failed_loads = [t for t in tests if is_load_failure(t)]
    runnable = [t for t in tests if not is_load_failure(t)]
    records: list[dict] = []
    if failed_loads:  # 导入失败的模块无法在工作进程中按名称重新加载：直接在这里运行得到错误详情
        res = RecordingResult(sys.stderr, True, 0)
        unittest.TestSuite(failed_loads).run(res)
        records += res.records
    try:
        times = json.loads(TIMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        times = {}
    buckets = plan(make_units(runnable), times, a.jobs)
    expected = [t.id() for t in runnable]
    print(f"并行运行 {len(expected)} 个测试（{len(buckets)} 个进程）…", flush=True)
    with tempfile.TemporaryDirectory(prefix="logo-selftest-") as td:
        procs = []
        for n, ids in enumerate(buckets):
            ids_file, out_file, log = (str(Path(td) / f"{n}.{s}") for s in ("ids.json", "out.json", "log"))
            Path(ids_file).write_text(json.dumps(ids), encoding="utf-8")
            handle = open(log, "w")
            procs.append((subprocess.Popen([sys.executable, str(Path(__file__)), "--worker", ids_file, out_file],
                                           cwd=HERE, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL),
                          ids, out_file, log, handle))
        try:
            pending = list(procs)
            deadline = started + a.timeout
            while pending:
                if time.time() > deadline:  # 超时：结束仍在运行的进程，其测试记为错误
                    for proc, ids, _, log, handle in pending:
                        proc.kill()
                        proc.wait()
                        handle.close()
                        records += [{"id": i, "status": "ERROR", "detail": f"超过总超时 {a.timeout}s，工作进程已终止",
                                     "seconds": 0.0, "fixture": False, "class": ""} for i in ids]
                        print(f"  ✗ 超时（{a.timeout}s），{len(ids)} 个测试记为错误", flush=True)
                    pending = []
                    break
                for item in list(pending):
                    proc, ids, out_file, log, handle = item
                    if proc.poll() is None:
                        continue
                    pending.remove(item)
                    handle.close()
                    if Path(out_file).exists():
                        part = json.loads(Path(out_file).read_text(encoding="utf-8"))
                        records += part
                        print(f"  进程完成：{len(ids)} 个测试，{sum(r['seconds'] for r in part):.0f}s", flush=True)
                    else:  # 异常退出：已收集的结果都没有写出，按未完成计入错误
                        tail = "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
                        records += [{"id": i, "status": "ERROR", "detail": f"工作进程异常退出（退出码 {proc.returncode}）\n{tail}",
                                     "seconds": 0.0, "fixture": False, "class": ""} for i in ids]
                        print(f"  ✗ 进程异常退出（退出码 {proc.returncode}），{len(ids)} 个测试记为错误", flush=True)
                time.sleep(0.2)
        except KeyboardInterrupt:
            for proc, *_, handle in procs:
                proc.terminate()
                handle.close()
            print("已中止")
            return 130
    missing = account(expected, records)
    for i in missing:
        records.append({"id": i, "status": "ERROR", "detail": "没有结果（可能漏跑）", "seconds": 0.0, "fixture": False, "class": ""})
    elapsed = time.time() - started
    if a.verbose:
        for r in sorted(records, key=lambda r: r["id"]):
            print(f"{r['id']} ... {r['status']} ({r['seconds']:.1f}s)")
    bad = [r for r in records if r["status"] in ("FAIL", "ERROR")]
    for r in bad:
        print("=" * 70 + f"\n{r['status']}: {r['id']}\n" + "-" * 70 + f"\n{r['detail']}")
    timed = {r["id"]: r["seconds"] for r in records if not r["fixture"] and r["status"] in ("ok", "FAIL")}
    if timed:
        merged = {**times, **timed}
        TIMES.parent.mkdir(parents=True, exist_ok=True)
        tmp = TIMES.with_name(f".{TIMES.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, indent=0), encoding="utf-8")
        os.replace(tmp, TIMES)
    ran = len([r for r in records if not r["fixture"]])
    print("-" * 70 + f"\nRan {ran} tests in {elapsed:.3f}s（{len(buckets)} 个进程）\n")
    fails = sum(r["status"] == "FAIL" for r in records)
    errors = sum(r["status"] == "ERROR" for r in records)
    skipped = sum(r["status"] == "skip" for r in records)
    if bad:
        print(f"FAILED (failures={fails}, errors={errors}" + (f", skipped={skipped}" if skipped else "") + ")")
        return 1
    print("OK" + (f" (skipped={skipped})" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
