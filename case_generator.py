"""测试用例生成模块：调用大语言模型基于接口结构生成结构化测试用例，支持 RAG 知识库增强（实验自变量）。"""
import json
import os
import re

import config
import kb

# 最近一次生成的元信息（供实验脚本统计“JSON 一次解析成功率”）
LAST_RUN_META = {"attempts": 0, "success_first_try": False}

SYSTEM_PROMPT = "你是一名资深接口测试工程师，精通等价类划分、边界值分析和异常场景设计，输出永远是严格的 JSON。"

# 用例生成提示词模板：{spec_json} 为接口结构，{rag_part} 为知识库注入段（可空）
PROMPT_TEMPLATE = """请根据下面的接口信息设计接口测试用例。

【接口信息】
{spec_json}

{rag_part}【生成要求】
1. 综合运用等价类划分法、边界值分析法、异常场景法，覆盖正常流与尽可能多的异常流（空值、超长、过短、非法类型、重复提交、特殊字符等）；
2. 每条用例必须属于且仅属于以下三类之一：等价类 / 边界值 / 异常场景；
3. 期望结果必须严格依据接口文档中的约定（HTTP 状态码、业务错误码），不要编造接口未定义的字段；
4. 生成 10~14 条用例；
5. 只输出一个 ```json 代码块，不要输出任何其他文字。JSON 数组元素结构如下：
[
  {{
    "name": "用例名称（中文，体现测试点）",
    "category": "等价类 / 边界值 / 异常场景 三选一",
    "method": "HTTP 方法（大写）",
    "url": "完整请求地址",
    "headers": {{"Content-Type": "application/json"}},
    "body": {{"字段名": "字段值"}},
    "files": {{}},
    "expected_status": 200,
    "assertion": "断言说明：除状态码外需校验的响应字段与期望值，格式如：字段 code 期望为 0"
  }}
]
6. body 为 JSON 对象（GET 等无请求体时为 {{}}）；expected_status 为整数。
7. 若接口是 multipart 文件上传（如 POST 且字段类型为 file），则 body 留空 {{}}，files 按场景填写系统约定的文件类型标记：
   - 合法图片正常上传：{{"字段名": "VALID_IMAGE"}}
   - 上传不支持的格式（如 pdf）：{{"字段名": "PDF"}}；上传 gif：{{"字段名": "GIF"}}
   - 上传空内容文件：{{"字段名": "EMPTY"}}
   - 不携带文件字段（测试必填校验）：{{"字段名": "NO_FILE"}}
   非文件上传接口，files 一律留 {{}}。
"""


def _build_rag_part(api_spec, use_rag):
    """use_rag 为 True 时检索知识库并拼入提示词；否则返回空串（对比实验的自变量）。"""
    if not use_rag:
        return ""
    query = "{} {} {} {}".format(
        api_spec.get("api_name", ""),
        api_spec.get("method", ""),
        api_spec.get("url", ""),
        " ".join(p.get("name", "") + " " + p.get("constraint", "") for p in api_spec.get("params", [])),
    )
    chunks = kb.retrieve(query, top_k=3)
    if not chunks:
        return ""
    joined = "\n\n".join("- " + c for c in chunks)
    return f"【测试方法知识库参考】\n{joined}\n\n"


def _extract_json_array(text):
    """先提取 ```json 代码块再 json.loads；无代码块时截取首个 JSON 数组。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = m.group(1).strip() if m else None
    if candidate is None:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
        else:
            candidate = text.strip()
    data = json.loads(candidate)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("解析结果不是 JSON 数组")
    return data


def _normalize_case(case, api_spec):
    """规整单条用例：补默认值、分类收敛到三类、method 大写。"""
    if not isinstance(case, dict):
        raise ValueError("用例不是 JSON 对象")
    category = str(case.get("category", ""))
    if category not in ("等价类", "边界值", "异常场景"):
        if "边界" in category:
            category = "边界值"
        elif "异常" in category or "场景" in category:
            category = "异常场景"
        else:
            category = "等价类"
    headers = case.get("headers")
    body = case.get("body")
    files = case.get("files")
    return {
        "name": str(case.get("name", "未命名用例")),
        "category": category,
        "method": str(case.get("method", api_spec.get("method", "GET"))).upper(),
        "url": str(case.get("url", api_spec.get("url", ""))),
        "headers": headers if isinstance(headers, dict) else {"Content-Type": "application/json"},
        "body": body if isinstance(body, (dict, list)) else {},
        "files": files if isinstance(files, dict) else {},
        "expected_status": int(case.get("expected_status", 200)),
        "assertion": str(case.get("assertion", "")),
    }


def generate_cases(api_spec, use_rag=True):
    """生成结构化测试用例列表，并保存到 generated_tests/cases_latest.json。

    use_rag=True 时检索知识库把相关测试方法段落注入提示词（论文对比实验的自变量）。
    输出解析失败时把错误反馈给大模型修正重试，最多 config.MAX_RETRIES 次。
    """
    global LAST_RUN_META
    prompt = PROMPT_TEMPLATE.format(
        spec_json=json.dumps(api_spec, ensure_ascii=False, indent=2),
        rag_part=_build_rag_part(api_spec, use_rag),
    )
    client = config.get_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    cases = []
    last_err = ""
    attempts = 0
    for attempts in range(1, config.MAX_RETRIES + 1):
        resp = client.chat.completions.create(
            model=config.MODEL, messages=messages, temperature=config.TEMPERATURE)
        content = resp.choices[0].message.content
        messages.append({"role": "assistant", "content": content})
        try:
            cases = [_normalize_case(c, api_spec) for c in _extract_json_array(content)]
            if not cases:
                raise ValueError("生成的用例数组为空")
            break
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = str(e)
            messages.append({
                "role": "user",
                "content": f"上一次输出解析失败：{last_err}。请修正后重新输出完整的 ```json 代码块（JSON 数组）。",
            })
    else:
        raise ValueError(f"用例生成失败，已重试 {config.MAX_RETRIES} 次：{last_err}")

    LAST_RUN_META = {"attempts": attempts, "success_first_try": attempts == 1}
    os.makedirs(config.GENERATED_DIR, exist_ok=True)
    out_path = os.path.join(config.GENERATED_DIR, "cases_latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    return cases
