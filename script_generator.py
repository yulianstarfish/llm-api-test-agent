"""pytest 脚本生成模块：把结构化测试用例转换为可运行的 pytest 脚本，写入 generated_tests/test_generated.py。"""
import glob
import os
import re

import config

# 生成脚本的头部（固定导入与常量）
HEADER = '''"""本文件由 script_generator.py 根据大模型生成的用例自动生成，请勿手工编辑。"""
# -*- coding: utf-8 -*-
import glob
import os
import tempfile

import requests

BASE_TIMEOUT = 10

# 文件上传类接口所用样例图片（取该目录下第一张 jpg/png）
SAMPLE_IMAGE_DIR = __SAMPLE_DIR__
_IMAGES = [f for f in glob.glob(os.path.join(SAMPLE_IMAGE_DIR, "*"))
           if f.lower().endswith((".jpg", ".jpeg", ".png"))]
SAMPLE_IMAGE = _IMAGES[0] if _IMAGES else None

# 临时构造的测试文件目录
_TMP_DIR = tempfile.mkdtemp(prefix="upload_cases_")


def _write_tmp(name, content):
    """把字节内容写入临时文件，返回路径。"""
    path = os.path.join(_TMP_DIR, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


# 构造非法格式 / 空内容文件，用于异常场景用例
_FAKE_PDF = _write_tmp("sample.pdf", b"%PDF-1.4\\n1 0 obj\\n<<>>\\nendobj\\ntrailer\\n<<>>\\n%%EOF")
_FAKE_GIF = _write_tmp(
    "sample.gif",
    b"GIF89a\\x01\\x00\\x01\\x00\\x80\\x00\\x00\\x00\\x00\\x00\\xff\\xff\\xff!\\xf9\\x04\\x01\\x00\\x00\\x00\\x00,"
    b"\\x00\\x00\\x00\\x00\\x01\\x00\\x01\\x00\\x00\\x02\\x02D\\x01\\x00;")
_FAKE_TXT = _write_tmp("sample.txt", b"this is a plain text file, not an image")
# 扩展名为 jpg 但内容不是图片（伪造文件）
_CORRUPTED_JPG = _write_tmp("fake.jpg", b"this is not real jpeg content, just text bytes")
_EMPTY_JPG = _write_tmp("empty.jpg", b"")

# 用例中的文件类型标记 → (上传文件名, 本地路径, MIME)
FILE_KINDS = {
    "VALID_IMAGE": ("sample.jpg", SAMPLE_IMAGE, "image/jpeg"),
    "PDF": ("sample.pdf", _FAKE_PDF, "application/pdf"),
    "GIF": ("sample.gif", _FAKE_GIF, "image/gif"),
    "TXT": ("sample.txt", _FAKE_TXT, "text/plain"),
    "CORRUPTED": ("fake.jpg", _CORRUPTED_JPG, "image/jpeg"),
    "EMPTY": ("empty.jpg", _EMPTY_JPG, "image/jpeg"),
}

'''

# 用例文档中的旧标记 → 新的文件类型标记
_LEGACY_TOKENS = {"__SAMPLE_IMAGE__": "VALID_IMAGE"}

# 文件类型推断规则：按优先级匹配用例名称/断言中的关键词（不依赖大模型严格遵守标记）
_INFER_RULES = [
    ("NO_FILE", ["不传", "缺少", "缺失", "未携带", "必填参数", "必填校验"]),
    ("PDF", ["pdf"]),
    ("GIF", ["gif"]),
    ("TXT", ["txt", "文本"]),
    ("CORRUPTED", ["损坏", "伪造", "非图片", "非法内容", "伪装"]),
    ("EMPTY", ["空文件", "空内容", "0字节", "0 字节", "大小为0"]),
]

# 已知标记集合
_KNOWN_TOKENS = {"VALID_IMAGE", "PDF", "GIF", "TXT", "CORRUPTED", "EMPTY", "NO_FILE"}


def _infer_file_kind(case, files=None):
    """根据用例名称与断言关键词推断文件类型；LLM 标记模糊/无效时作为兜底。

    files 参数传入 _gen_case_func 兜底后的 files（可能与 case["files"] 不同）。
    """
    files = files if files is not None else (case.get("files") or {})
    token = next(iter(files.values()), None) if files else None
    # 只有 LLM 给出明确的新标记才直接采信；旧通用标记（__SAMPLE_IMAGE__）一律走关键词推断
    if token in _KNOWN_TOKENS:
        return token
    text = (case.get("name", "") + " " + case.get("assertion", "")).lower()
    for kind, keywords in _INFER_RULES:
        if any(k in text for k in keywords):
            return kind
    return "VALID_IMAGE"

# 从断言自然语言中提取“字段 xxx 期望为/为/等于/= 值”的保守模式
_ASSERT_RE = re.compile(
    r"字段\s*[`'\"“”]?([A-Za-z_][A-Za-z0-9_]*)[`'\"“”]?\s*"
    r"(?:期望为|期望值是|应为|等于|为|=|==)\s*"
    r"(`[^`]*`|\"[^\"]*\"|'[^']*'|“[^”]*”|-?\d+(?:\.\d+)?|true|false|True|False)",
    re.I,
)

# 特殊断言模式：范围 / 大于 / 小于 / 非空字符串（优先于精确等于模式）
# 前缀兼容 LLM 的各种措辞：期望为 / 期望值是 / 应为 / 为 / 省略
_FIELD = r"字段\s*[`'\"“”]?([A-Za-z_][A-Za-z0-9_]*)[`'\"“”]?\s*(?:期望为|期望值是|应为|为)?\s*"
_RANGE_RE = re.compile(_FIELD + r"(?:介于|在)?\s*(-?\d+(?:\.\d+)?)\s*(?:[~～]|到|至)\s*(-?\d+(?:\.\d+)?)\s*之间")
_GT_RE = re.compile(_FIELD + r"大于\s*(-?\d+(?:\.\d+)?)")
_LT_RE = re.compile(_FIELD + r"小于\s*(-?\d+(?:\.\d+)?)")
_NONEMPTY_RE = re.compile(_FIELD + r"非空")


def _slugify(name, index):
    """把用例名转换为合法的 pytest 函数名（保留中文，Python3 支持中文标识符）。"""
    slug = re.sub(r"[^0-9A-Za-z一-鿿_]+", "_", name).strip("_")
    if not slug:
        slug = f"case_{index}"
    if slug[0].isdigit():
        slug = "c_" + slug
    return f"test_{index:02d}_{slug}"


def _parse_expected_value(raw):
    """把断言里提取出的期望值字面量转换为 Python 值。"""
    raw = raw.strip().strip("`").strip()
    if len(raw) >= 2 and raw[0] == "“" and raw[-1] == "”":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _extract_field_assertions(assertion):
    """从断言说明中提取字段断言，返回 (字段, 操作, 参数) 列表。

    操作类型：eq 精确等于 / range 数值范围 / gt 大于 / lt 小于 / nonempty 非空字符串。
    特殊模式优先解析，已处理字段不再走精确等于兜底，避免“0~1 之间”被误解析成等于 0。
    """
    text = assertion or ""
    results = []
    handled = set()
    for m in _RANGE_RE.finditer(text):
        field, lo, hi = m.group(1), float(m.group(2)), float(m.group(3))
        results.append((field, "range", (lo, hi)))
        handled.add(field)
    for regex, op in ((_GT_RE, "gt"), (_LT_RE, "lt")):
        for m in regex.finditer(text):
            field = m.group(1)
            if field in handled:
                continue
            results.append((field, op, float(m.group(2))))
            handled.add(field)
    for m in _NONEMPTY_RE.finditer(text):
        field = m.group(1)
        if field in handled:
            continue
        results.append((field, "nonempty", None))
        handled.add(field)
    for m in _ASSERT_RE.finditer(text):
        field = m.group(1)
        if field in handled:
            continue
        value = _parse_expected_value(m.group(2))
        item = (field, "eq", value)
        if item not in results:
            results.append(item)
    return results


def _gen_case_func(case, index):
    """生成单条用例对应的 test_ 函数源码。"""
    fname = _slugify(case["name"], index)
    safe_assertion = (case.get("assertion") or "").replace("\n", " ").strip()
    expected = int(case.get("expected_status", 200))
    method = str(case.get("method", "GET")).upper()
    files = case.get("files") or {}
    # 文件上传接口但 LLM 未填 files：按用例意图兜底
    if not files and method == "POST" and not case.get("body"):
        _t = case.get("name", "") + case.get("assertion", "")
        if any(k in _t for k in ["不传", "缺少", "缺失", "必填", "未携带"]):
            files = {"file": "NO_FILE"}
        elif any(k in _t for k in ["上传", "图片", "文件"]):
            # 明显是上传场景的用例但 LLM 漏填 files：按合法图片处理
            files = {"file": "VALID_IMAGE"}

    lines = [
        f"def {fname}():",
        f'    """[{case["category"]}] {case["name"]}"""',
        f"    # 断言说明：{safe_assertion}",
    ]

    if files:
        # 文件上传用例：按推断出的文件类型构造真实上传文件
        field = next(iter(files))
        token = _infer_file_kind(case, files)
        headers = {k: v for k, v in (case.get("headers") or {}).items()
                   if k.lower() != "content-type"}
        if token == "NO_FILE":
            # 不携带文件字段：直接发请求
            lines.append(
                f"    resp = requests.request(method={method!r}, url={case['url']!r}, "
                f"headers={headers!r}, timeout=BASE_TIMEOUT)")
        else:
            lines.append(f"    _kind = FILE_KINDS.get({token!r})")
            lines.append("    if _kind is None or not _kind[1] or not os.path.exists(_kind[1]):")
            lines.append("        pytest.skip('缺少可用的测试文件，跳过该用例')")
            lines.append("    _name, _path, _mime = _kind")
            lines.append("    with open(_path, 'rb') as _f:")
            lines.append(
                f"        resp = requests.request(method={method!r}, url={case['url']!r}, "
                f"headers={headers!r}, files={{{field!r}: (_name, _f, _mime)}}, "
                "timeout=BASE_TIMEOUT)")
    else:
        kwargs = [f"method={method!r}", f"url={case['url']!r}", f"headers={case['headers']!r}"]
        # 只有带请求体语义的方法或非空 body 才传 json，避免 GET 携带多余请求体
        if method in ("POST", "PUT", "PATCH", "DELETE") or case.get("body"):
            kwargs.append(f"json={case['body']!r}")
        kwargs.append("timeout=BASE_TIMEOUT")
        lines.append(f"    resp = requests.request({', '.join(kwargs)})")

    lines.append(
        f'    assert resp.status_code == {expected}, f"期望状态码 {expected}，实际 {{resp.status_code}}，响应内容：{{resp.text[:200]}}"')

    pairs = _extract_field_assertions(safe_assertion)
    if pairs:
        lines.append("    data = resp.json()")
        for field, op, value in pairs:
            if op == "range":
                lo, hi = value
                lines.append(f"    # 响应字段断言：{field} 期望为 {lo}~{hi} 之间的数值")
                lines.append(f"    assert {lo} <= data.get({field!r}) <= {hi}")
            elif op == "gt":
                lines.append(f"    # 响应字段断言：{field} 期望大于 {value}")
                lines.append(f"    assert data.get({field!r}) > {value}")
            elif op == "lt":
                lines.append(f"    # 响应字段断言：{field} 期望小于 {value}")
                lines.append(f"    assert data.get({field!r}) < {value}")
            elif op == "nonempty":
                lines.append(f"    # 响应字段断言：{field} 期望为非空字符串")
                lines.append(f"    assert isinstance(data.get({field!r}), str) and data.get({field!r})")
            else:
                lines.append(f"    # 响应字段断言：{field} 期望为 {value!r}")
                lines.append(f"    assert data.get({field!r}) == {value!r}")
    else:
        lines.append("    # 未从断言说明中解析出明确的字段期望值，仅断言状态码（保守策略）")
    return "\n".join(lines)


def generate_script(cases):
    """把用例列表转换为完整的 pytest 脚本字符串。"""
    parts = [HEADER.replace("__SAMPLE_DIR__", repr(config.SAMPLE_IMAGE_PATH))]
    # 文件上传用例会用到 pytest.skip，需要额外导入
    if any(c.get("files") for c in cases):
        parts.append("import pytest\n\n\n")
    for i, case in enumerate(cases, 1):
        parts.append(_gen_case_func(case, i))
        parts.append("\n\n")
    return "".join(parts)


def write_script(cases, path=None):
    """生成 pytest 脚本并写入 generated_tests/test_generated.py，返回文件路径。"""
    path = path or os.path.join(config.GENERATED_DIR, "test_generated.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(generate_script(cases))
    return path
