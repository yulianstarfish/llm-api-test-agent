"""对比实验脚本（论文第 6 章数据）：RAG 增强开/关两种条件下各生成 N 次用例，统计质量指标并输出 CSV。

用法：
    1. 本脚本会在每轮执行前自动重启被测系统（sut/demo_api.py）以保证内存数据干净；
    2. 直接运行：python experiments/run_experiment.py
结果写入 experiments/results.csv。
"""
import csv
import os
import subprocess
import sys
import time

import requests

# 把项目根目录加入 sys.path，保证能 import 项目模块（本脚本位于 experiments/ 子目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_generator
import config
import doc_parser
import executor
import script_generator

N_RUNS = 3           # 每种条件下生成次数
SUT_BASE = "http://127.0.0.1:8001"

# 示例接口文档（被测系统 demo_api 的用户注册接口，两个故意缺陷都在其中）
DEMO_DOC = """用户注册接口文档

接口名称：用户注册
URL：POST http://127.0.0.1:8001/register
请求体（JSON）：
  username：string，必填，长度 3-20，全局唯一
  password：string，必填，长度 6-32
响应说明：
  注册成功：HTTP 200，{"code": 0, "msg": "注册成功"}
  用户名已存在（重复注册）：HTTP 409，{"code": 1001, "msg": "用户已存在"}
  参数非法（用户名长度不在 3-20、密码长度不在 6-32、缺少必填字段）：HTTP 400，{"code": 1003, "msg": "参数错误"}
"""

# 预定义 10 个需求点：(需求点名, [命中关键词])，检查用例 name/assertion 是否命中
REQUIREMENT_POINTS = [
    ("空用户名注册", ["空用户名", "用户名为空", "用户名空"]),
    ("用户名过短注册（<3）", ["过短", "少于3", "小于3", "2位", "不足3"]),
    ("用户名过长注册（>20）", ["用户名过长", "超过20", "21位", "大于20"]),
    ("空密码注册", ["空密码", "密码为空"]),
    ("密码过短注册（<6）", ["密码过短", "少于6", "小于6", "5位", "不足6"]),
    ("超长密码注册（>32）", ["超长密码", "超过32", "33位", "大于32", "密码过长"]),
    ("重复用户名注册", ["重复注册", "重复用户名", "已存在"]),
    ("合法参数正常注册", ["正常注册", "合法", "有效等价类", "注册成功"]),
    ("字段类型非法", ["类型非法", "非法类型", "非字符串", "类型错误", "数字作为"]),
    ("特殊字符/SQL注入注册", ["注入", "特殊字符", "sql", "' or ", "xss"]),
]

# 两个故意缺陷对应的需求点下标与失败用例名关键词
BUG1_KEYWORDS = REQUIREMENT_POINTS[5][1]   # 超长密码被接受（期望 400 实际 200）
BUG2_KEYWORDS = REQUIREMENT_POINTS[6][1]   # 重复注册应返回 409（实际 200）

CSV_HEADER = ["条件", "轮次", "用例总数", "JSON一次解析成功", "需求覆盖数(/10)",
              "需求覆盖率", "检出缺陷数(/2)", "检出缺陷明细", "备注"]


def wait_for_sut(timeout=20):
    """轮询等待被测系统端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{SUT_BASE}/users/__ping__", timeout=2)
            return True
        except requests.RequestException:
            time.sleep(0.5)
    return False


def sut_running():
    try:
        requests.get(f"{SUT_BASE}/users/__ping__", timeout=2)
        return True
    except requests.RequestException:
        return False


def restart_sut():
    """重启被测系统子进程以获得干净的内存数据，返回 Popen 对象。"""
    sut_path = os.path.join(config.PROJECT_ROOT, "sut", "demo_api.py")
    proc = subprocess.Popen([sys.executable, sut_path], cwd=config.PROJECT_ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_for_sut():
        proc.terminate()
        raise RuntimeError("被测系统启动失败，请检查 8001 端口是否被占用")
    return proc


def stop_sut(proc):
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def point_hit(cases, keywords):
    """某需求点是否被至少一条用例命中（匹配 name + assertion）。"""
    for c in cases:
        text = (c.get("name", "") + " " + c.get("assertion", "")).lower()
        if any(k.lower() in text for k in keywords):
            return True
    return False


def detect_bugs(cases, result):
    """判断两个故意缺陷是否被检出：对应测试点的用例存在且执行失败（接口行为与预期不符）。"""
    failed_names = " ".join(f.get("case_name", "") for f in result.get("failures", []))
    bug1 = point_hit(cases, BUG1_KEYWORDS) and any(k.lower() in failed_names.lower() for k in BUG1_KEYWORDS)
    bug2 = point_hit(cases, BUG2_KEYWORDS) and any(k.lower() in failed_names.lower() for k in BUG2_KEYWORDS)
    details = []
    if bug1:
        details.append("Bug1:超长密码未校验")
    if bug2:
        details.append("Bug2:重复注册状态码错误")
    return (int(bug1) + int(bug2)), "；".join(details)


def run_one(use_rag, round_idx, api_spec):
    """执行一轮：生成用例 → 写脚本 → 重启被测系统 → 执行测试 → 统计指标。"""
    condition = "RAG开" if use_rag else "RAG关"
    row = {"条件": condition, "轮次": round_idx, "备注": ""}
    try:
        cases = case_generator.generate_cases(api_spec, use_rag=use_rag)
    except Exception as e:
        row.update({"用例总数": 0, "JSON一次解析成功": "失败", "需求覆盖数(/10)": 0,
                    "需求覆盖率": "0%", "检出缺陷数(/2)": 0, "检出缺陷明细": "",
                    "备注": f"生成失败：{e}"})
        return row
    meta = dict(case_generator.LAST_RUN_META)
    script_generator.write_script(cases)

    proc = restart_sut()
    try:
        result = executor.run_tests()
    finally:
        stop_sut(proc)

    covered = sum(1 for _, kws in REQUIREMENT_POINTS if point_hit(cases, kws))
    bug_cnt, bug_detail = detect_bugs(cases, result)
    row.update({
        "用例总数": len(cases),
        "JSON一次解析成功": "是" if meta.get("success_first_try") else f"否(第{meta.get('attempts')}次成功)",
        "需求覆盖数(/10)": covered,
        "需求覆盖率": f"{covered * 10}%",
        "检出缺陷数(/2)": bug_cnt,
        "检出缺陷明细": bug_detail or "未检出",
        "备注": f"执行：{result['passed']}通过/{result['failed']}失败",
    })
    return row


def summarize(rows, use_rag):
    """对同一条件下的多轮结果求汇总均值行。"""
    condition = "RAG开" if use_rag else "RAG关"
    valid = [r for r in rows if r["条件"] == condition and r["用例总数"]]
    if not valid:
        return {"条件": f"{condition}-汇总", "轮次": "平均", "备注": "无有效轮次"}
    n = len(valid)
    avg_cases = sum(r["用例总数"] for r in valid) / n
    first_try = sum(1 for r in valid if r["JSON一次解析成功"] == "是")
    avg_cover = sum(r["需求覆盖数(/10)"] for r in valid) / n
    avg_bug = sum(r["检出缺陷数(/2)"] for r in valid) / n
    return {
        "条件": f"{condition}-汇总", "轮次": "平均",
        "用例总数": round(avg_cases, 1),
        "JSON一次解析成功": f"{first_try}/{n}",
        "需求覆盖数(/10)": round(avg_cover, 1),
        "需求覆盖率": f"{round(avg_cover * 10)}%",
        "检出缺陷数(/2)": round(avg_bug, 2),
        "检出缺陷明细": "", "备注": "",
    }


def main():
    if sut_running():
        print("警告：8001 端口已有被测系统在运行，将直接复用（内存数据可能不干净，建议先关闭旧进程）。")
    print("【准备】调用 DeepSeek 解析示例接口文档 ...")
    api_spec = doc_parser.parse_api_doc(DEMO_DOC)
    print(f"【准备】解析结果：{api_spec['api_name']} {api_spec['method']} {api_spec['url']}")

    rows = []
    total = N_RUNS * 2
    done = 0
    for use_rag in (True, False):
        for i in range(1, N_RUNS + 1):
            done += 1
            print(f"\n【{done}/{total}】use_rag={use_rag} 第 {i} 轮：生成用例中 ...")
            row = run_one(use_rag, i, api_spec)
            rows.append(row)
            print(f"    用例 {row['用例总数']} 条，覆盖 {row.get('需求覆盖数(/10)', 0)}/10，"
                  f"检出缺陷 {row.get('检出缺陷数(/2)', 0)}/2")

    rows.append(summarize(rows, True))
    rows.append(summarize(rows, False))

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n实验完成，结果已写入：{out_path}")
    for r in rows[-2:]:
        print(f"  {r['条件']}：平均用例 {r.get('用例总数')}，一次解析成功 {r.get('JSON一次解析成功')}，"
              f"平均覆盖 {r.get('需求覆盖数(/10)')}/10，平均检出缺陷 {r.get('检出缺陷数(/2)')}/2")


if __name__ == "__main__":
    main()
