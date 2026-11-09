"""测试执行模块：以子进程方式运行 pytest 执行生成的脚本，解析输出并返回统计信息与失败明细。"""
import os
import re
import subprocess
import sys

import config


def _count(text, pattern):
    """统计 pytest 汇总行中 passed/failed/error 的数量（取所有匹配之和）。"""
    return sum(int(x) for x in re.findall(pattern, text))


def _parse_failures(output):
    """从 pytest -v 输出中解析失败用例：[(用例名, 错误摘要)]，保持出现顺序去重。"""
    failures = []
    # short test summary info 中的行：FAILED <文件>::<用例名> - <错误摘要>
    for m in re.finditer(r"^FAILED\s+\S*?::([^\s]+?)\s*-\s*(.+)$", output, re.M):
        failures.append({"case_name": m.group(1).strip(), "error": m.group(2).strip()[:300]})
    if not failures:
        # 兼容无摘要区的情况：从进度行中抓 “::用例名 FAILED”
        for m in re.finditer(r"::(\S+)\s+FAILED", output):
            failures.append({"case_name": m.group(1).strip(), "error": ""})
    seen = set()
    uniq = []
    for f in failures:
        if f["case_name"] not in seen:
            seen.add(f["case_name"])
            uniq.append(f)
    return uniq


def _tail(text, n=5):
    """取输出最后几行作为兜底错误摘要。"""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])[:500]


def run_tests(script_path=None, timeout=120):
    """运行 pytest 执行生成的测试脚本，返回执行结果字典。

    返回：{passed, failed, total, failures, report_path, raw_output}
    failures 元素为 {"case_name": 失败用例名, "error": 错误摘要}。
    使用 sys.executable 保证调用的是当前 venv 的 python。
    """
    script_path = script_path or os.path.join(config.GENERATED_DIR, "test_generated.py")
    report_path = os.path.join(config.GENERATED_DIR, "report.html")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"测试脚本不存在：{script_path}，请先生成用例")

    cmd = [sys.executable, "-m", "pytest", script_path,
           f"--html={report_path}", "--self-contained-html", "-v"]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # 保证子进程中文输出可被正确解码
    try:
        proc = subprocess.run(cmd, cwd=config.PROJECT_ROOT, capture_output=True,
                              timeout=timeout, env=env, encoding="utf-8", errors="replace")
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return {
            "passed": 0, "failed": 0, "total": 0,
            "failures": [{"case_name": "执行超时", "error": f"pytest 执行超过 {timeout} 秒被终止"}],
            "report_path": report_path, "raw_output": "",
        }

    passed = _count(output, r"(\d+)\s+passed")
    failed = _count(output, r"(\d+)\s+failed")
    errors = _count(output, r"(\d+)\s+errors?")
    failures = _parse_failures(output)
    if not failures and (failed + errors) > 0:
        failures.append({"case_name": "未知失败", "error": _tail(output)})
    return {
        "passed": passed,
        "failed": failed + errors,
        "total": passed + failed + errors,
        "failures": failures,
        "report_path": report_path,
        "raw_output": output,
    }
