"""接口文档解析模块：将用户粘贴的接口文档文本解析为结构化字典（api_name/url/method/params/returns）。"""
import json
import re

import config

# 让大模型把自然语言接口文档整理为严格 JSON 的提示词
PARSE_PROMPT = """你是一名接口测试专家。请把下面的接口文档文本解析为结构化 JSON。

要求：
1. 只输出一个 ```json 代码块，不要输出任何解释性文字；
2. JSON 结构如下：
{
  "api_name": "接口名称",
  "url": "完整请求地址（含协议与主机，若文档只给路径则原样保留）",
  "method": "HTTP 方法（大写，如 GET/POST）",
  "params": [
    {"name": "参数名", "type": "参数类型", "required": true, "constraint": "约束说明，无则为空字符串"}
  ],
  "returns": "响应说明（成功与失败的 HTTP 状态码、响应体结构、业务错误码）"
}
3. 若文档包含多个接口，解析第一个或最主要的一个；
4. 文档未提及的字段按上述默认值处理，不要编造参数。

接口文档如下：
"""


def _extract_json_block(text):
    """从模型输出中提取 ```json ...``` 代码块内容；若无代码块则截取首个 JSON 对象。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def _normalize(spec):
    """规整解析结果：补全缺失键、统一 method 大写、保证 params 元素字段齐全。"""
    if not isinstance(spec, dict):
        raise ValueError("解析结果不是 JSON 对象")
    norm_params = []
    for p in spec.get("params") or []:
        if not isinstance(p, dict):
            continue
        norm_params.append({
            "name": str(p.get("name", "")),
            "type": str(p.get("type", "string")),
            "required": bool(p.get("required", False)),
            "constraint": str(p.get("constraint", "") or ""),
        })
    returns = spec.get("returns", "")
    if not isinstance(returns, str):
        returns = json.dumps(returns, ensure_ascii=False)
    return {
        "api_name": str(spec.get("api_name", "未命名接口")),
        "url": str(spec.get("url", "")),
        "method": str(spec.get("method", "GET")).upper(),
        "params": norm_params,
        "returns": returns,
    }


def _try_direct_parse(raw_text):
    """若输入本身是 JSON / OpenAPI 文本则直接解析为内部结构；解析不了返回 None。"""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    # 情况一：已经是内部结构（含 url 与 method 字段）
    if "url" in data and "method" in data:
        return _normalize(data)
    # 情况二：OpenAPI 文档，取 paths 下第一个路径的第一个 HTTP 方法
    paths = data.get("paths")
    if isinstance(paths, dict):
        for path, ops in paths.items():
            if not isinstance(ops, dict):
                continue
            for method, op in ops.items():
                if method.lower() not in ("get", "post", "put", "delete", "patch"):
                    continue
                op = op or {}
                params = []
                for p in op.get("parameters") or []:
                    schema = p.get("schema") or {}
                    params.append({
                        "name": p.get("name", ""),
                        "type": schema.get("type", "string"),
                        "required": bool(p.get("required", False)),
                        "constraint": p.get("description", "") or "",
                    })
                # requestBody(application/json) 的属性也并入参数列表
                body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
                body_schema = body.get("schema") or {}
                required = set(body_schema.get("required") or [])
                for name, prop in (body_schema.get("properties") or {}).items():
                    prop = prop or {}
                    params.append({
                        "name": name,
                        "type": prop.get("type", "string"),
                        "required": name in required,
                        "constraint": prop.get("description", "") or "",
                    })
                base = ""
                if isinstance(data.get("servers"), list) and data["servers"]:
                    base = (data["servers"][0] or {}).get("url", "")
                return _normalize({
                    "api_name": op.get("summary") or op.get("operationId") or f"{method.upper()} {path}",
                    "url": base.rstrip("/") + path if base else path,
                    "method": method.upper(),
                    "params": params,
                    "returns": json.dumps(op.get("responses", {}), ensure_ascii=False)[:800],
                })
    return None


def parse_api_doc(raw_text):
    """把接口文档文本解析为结构化 dict。

    优先尝试按 JSON/OpenAPI 直接解析；否则调用大模型解析，
    解析失败时把错误信息反馈给大模型修正重试，最多 config.MAX_RETRIES 次。
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("接口文档内容为空")
    direct = _try_direct_parse(raw_text)
    if direct is not None:
        return direct

    client = config.get_client()
    messages = [
        {"role": "system", "content": "你是一名资深接口测试工程师，擅长把中文接口文档整理为严格的 JSON 结构。"},
        {"role": "user", "content": PARSE_PROMPT + raw_text},
    ]
    last_err = ""
    for _ in range(1, config.MAX_RETRIES + 1):
        resp = client.chat.completions.create(
            model=config.MODEL, messages=messages, temperature=config.TEMPERATURE)
        content = resp.choices[0].message.content
        messages.append({"role": "assistant", "content": content})
        try:
            return _normalize(json.loads(_extract_json_block(content)))
        except (json.JSONDecodeError, ValueError) as e:
            last_err = str(e)
            messages.append({
                "role": "user",
                "content": f"上一次输出无法解析为 JSON，错误：{last_err}。请修正后重新只输出一个 ```json 代码块。",
            })
    raise ValueError(f"接口文档解析失败，已重试 {config.MAX_RETRIES} 次：{last_err}")
