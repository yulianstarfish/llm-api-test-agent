"""失败归因分析模块：对执行失败的测试用例逐条调用大语言模型归因，输出结构化分析结果。"""
import json
import re

import config

SYSTEM_PROMPT = "你是一名资深测试架构师，擅长分析接口测试失败原因，输出严格的 JSON。"

# 归因提示词模板：{spec_json} 为接口结构，{failure_json} 为失败用例与错误信息
PROMPT_TEMPLATE = """下面是一条执行失败的接口测试用例及其错误信息，请对失败原因进行归因。

【接口信息】
{spec_json}

【失败用例与错误信息】
{failure_json}

归类标准：
- 需求理解错误：大模型对接口文档的约束理解有误，导致用例的预期结果不符合需求；
- 接口缺陷：被测接口的实际行为与文档约定不符，大概率是接口实现的 bug；
- 用例本身错误：用例的请求构造有误（字段名、字段类型、请求格式错误等）。

请分析后只输出一个 ```json 代码块，不要输出其他文字，结构如下：
{{
  "case_name": "失败用例名",
  "category": "需求理解错误 / 接口缺陷 / 用例本身错误 三选一",
  "reason": "失败原因分析（中文，一两句话）",
  "suggestion": "修复建议（中文，一两句话）"
}}
"""


def _extract_json_object(text):
    """从模型输出中提取 JSON 对象（优先 ```json 代码块）。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return json.loads(m.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出中未找到 JSON 对象")
    return json.loads(text[start:end + 1])


def _analyze_one(client, failure, spec_json):
    """对单条失败用例归因；解析失败重试最多 config.MAX_RETRIES 次。"""
    prompt = PROMPT_TEMPLATE.format(
        spec_json=spec_json,
        failure_json=json.dumps(failure, ensure_ascii=False),
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    last_err = ""
    for _ in range(1, config.MAX_RETRIES + 1):
        resp = client.chat.completions.create(
            model=config.MODEL, messages=messages, temperature=config.TEMPERATURE)
        content = resp.choices[0].message.content
        messages.append({"role": "assistant", "content": content})
        try:
            data = _extract_json_object(content)
            category = str(data.get("category", ""))
            if category not in ("需求理解错误", "接口缺陷", "用例本身错误"):
                category = "用例本身错误"
            return {
                "case_name": str(data.get("case_name", failure.get("case_name", ""))),
                "category": category,
                "reason": str(data.get("reason", "")),
                "suggestion": str(data.get("suggestion", "")),
            }
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
            last_err = str(e)
            messages.append({
                "role": "user",
                "content": f"上一次输出无法解析为 JSON：{last_err}。请只输出修正后的 ```json 代码块。",
            })
    # 重试耗尽，返回兜底结果而不是中断整体分析
    return {
        "case_name": failure.get("case_name", ""),
        "category": "用例本身错误",
        "reason": f"归因分析失败（大模型输出多次无法解析）：{last_err}",
        "suggestion": "请人工检查该用例的请求构造与期望结果。",
    }


def analyze_failures(failures, api_spec):
    """对每条失败用例调用大模型归因，返回 [{case_name, category, reason, suggestion}]。"""
    results = []
    if not failures:
        return results
    client = config.get_client()
    spec_json = json.dumps(api_spec, ensure_ascii=False, indent=2)
    for failure in failures:
        results.append(_analyze_one(client, failure, spec_json))
    return results
