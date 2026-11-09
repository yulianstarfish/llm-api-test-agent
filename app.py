"""系统入口：基于 Gradio 的中文 Web 界面，串联“文档解析 → 用例生成 → 脚本执行 → 失败归因”完整流程。"""
import os

import gradio as gr
import requests

import analyzer
import case_generator
import config
import doc_parser
import executor
import script_generator

# 内置示例1：葡萄叶片病害识别 API（真实 AI 服务，论文主要被测对象）
EXAMPLE_DOC_GRAPE = """葡萄叶片病害识别服务接口文档

一、健康检查
- URL：GET http://127.0.0.1:8002/health
- 无请求参数
- 响应：HTTP 200，{"status": "ok", "model_loaded": false}

二、病害识别（文件上传）
- URL：POST http://127.0.0.1:8002/predict
- 请求格式：multipart/form-data
- 请求字段：file，file 类型，必填，仅支持 jpg/jpeg/png 格式
- 响应：
  - 识别成功：HTTP 200，{"disease_en": "Grape___Black_rot", "disease_cn": "黑腐病", "confidence": 1.0, "inference_time_ms": 20.5}
  - 文件格式不支持（上传 txt/pdf 等）：HTTP 400，{"error": "仅支持 jpg/jpeg/png 图片"}
"""

# 内置示例2：业务接口（sut/demo_api.py，含 2 个故意缺陷，适合缺陷检出率实验）
EXAMPLE_DOC = """用户注册 / 登录 / 查询接口文档

一、用户注册
- 接口名称：用户注册
- URL：POST http://127.0.0.1:8001/register
- 请求体（JSON）：
  - username：string，必填，长度 3-20，全局唯一
  - password：string，必填，长度 6-32
- 响应：
  - 注册成功：HTTP 200，{"code": 0, "msg": "注册成功"}
  - 用户名已存在（重复注册）：HTTP 409，{"code": 1001, "msg": "用户已存在"}
  - 参数非法（用户名长度不在 3-20、密码长度不在 6-32、缺少必填字段）：HTTP 400，{"code": 1003, "msg": "参数错误"}

二、用户登录
- URL：POST http://127.0.0.1:8001/login
- 请求体（JSON）：username 必填；password 必填
- 响应：成功 HTTP 200，{"code": 0, "msg": "登录成功", "token": "..."}；失败 HTTP 401，{"code": 1002, "msg": "用户名或密码错误"}

三、查询用户
- URL：GET http://127.0.0.1:8001/users/{username}
- 路径参数：username，string，必填
- 响应：存在 HTTP 200，{"code": 0, "data": {"username": "..."}}；不存在 HTTP 404，{"code": 1004, "msg": "用户不存在"}
"""

CASE_HEADERS = ["用例名称", "分类", "方法", "URL", "期望状态码", "断言说明"]


def _cases_to_rows(cases):
    """把用例 dict 列表转换为 Dataframe 行数据。"""
    return [[c["name"], c["category"], c["method"], c["url"], c["expected_status"], c["assertion"]]
            for c in cases]


def fill_example():
    """“填入示例”按钮：返回内置示例接口文档（默认葡萄识别 API）。"""
    return EXAMPLE_DOC_GRAPE


def fill_example_demo():
    """“填入业务接口示例”按钮：返回含故意缺陷的注册登录接口文档。"""
    return EXAMPLE_DOC


def generate(doc_text, use_rag, state):
    """① 生成用例：解析文档 → 大模型生成用例 → 自动写出 pytest 脚本。"""
    if not doc_text or not doc_text.strip():
        return [], state, "请先粘贴接口文档，或点击「填入示例」。"
    try:
        api_spec = doc_parser.parse_api_doc(doc_text)
        cases = case_generator.generate_cases(api_spec, use_rag=use_rag)
        script_path = script_generator.write_script(cases)
        state["api_spec"] = api_spec
        state["cases"] = cases
        state.pop("last_result", None)
        msg = (f"生成成功：共 {len(cases)} 条用例（RAG 增强：{'开' if use_rag else '关'}）。\n\n"
               f"pytest 脚本已写入：`{script_path}`\n\n"
               f"请确保被测系统已启动（`python sut/demo_api.py`），再点击「② 执行测试」。")
        return _cases_to_rows(cases), state, msg
    except Exception as e:
        return [], state, f"生成失败：{e}"


def execute(state):
    """② 执行测试：运行 pytest 并汇总统计、产出 HTML 报告。"""
    if not state.get("cases"):
        return {"提示": "请先点击「① 生成用例」"}, None, state
    try:
        result = executor.run_tests()
    except Exception as e:
        return {"错误": str(e)}, None, state
    state["last_result"] = result
    stats = {
        "通过": result["passed"],
        "失败": result["failed"],
        "总数": result["total"],
        "失败用例": [f["case_name"] for f in result["failures"]],
    }
    report = result["report_path"] if os.path.exists(result["report_path"]) else None
    return stats, report, state


def analyze(state):
    """③ 失败归因：对执行失败的用例逐条调用大模型归因并以表格展示。"""
    result = state.get("last_result")
    api_spec = state.get("api_spec")
    if not result or not api_spec:
        return "请依次完成「① 生成用例」和「② 执行测试」。"
    failures = result.get("failures", [])
    if not failures:
        return "全部用例通过，无需归因。"
    try:
        items = analyzer.analyze_failures(failures, api_spec)
    except Exception as e:
        return f"归因分析失败：{e}"
    lines = ["| 失败用例 | 归因分类 | 原因分析 | 修复建议 |", "| --- | --- | --- | --- |"]
    for it in items:
        row = [str(it.get(k, "")).replace("|", "\\|").replace("\n", " ")
               for k in ("case_name", "category", "reason", "suggestion")]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def recognize_leaf(image_path):
    """病害识别演示：上传叶片图片，调用被测服务 8002 识别并展示结果。"""
    if not image_path:
        return None, "请先上传葡萄叶片图片"
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(f"{config.GRAPE_API_BASE}/predict",
                                 files={"file": ("leaf.jpg", f, "image/jpeg")}, timeout=60)
    except requests.exceptions.ConnectionError:
        return None, "无法连接识别服务（8002 端口），请先运行 sut/grape_api.py"
    if resp.status_code == 200:
        d = resp.json()
        text = (f"### 识别结果\n"
                f"- **病害类型**：{d.get('disease_cn')}（{d.get('disease_en')}）\n"
                f"- **置信度**：{d.get('confidence')}\n"
                f"- **推理耗时**：{d.get('inference_time_ms')} ms")
    else:
        text = f"### 识别失败\nHTTP {resp.status_code}：{resp.text[:200]}"
    return image_path, text


def build_app():
    with gr.Blocks(title="基于大语言模型的接口测试用例自动生成系统") as demo:
        gr.Markdown("# 基于大语言模型的接口测试用例自动生成系统")
        with gr.Tabs():
            with gr.Tab("测试用例生成"):
                gr.Markdown("流程：粘贴接口文档 → ① 生成用例（可选 RAG 知识库增强）→ ② 执行测试 → ③ 失败归因")
                state = gr.State({})
                with gr.Row():
                    with gr.Column(scale=1):
                        doc_input = gr.Textbox(label="接口文档", lines=20,
                                               placeholder="粘贴接口文档文本（也支持 OpenAPI/JSON 格式）")
                        with gr.Row():
                            example_btn = gr.Button("填入葡萄识别API示例")
                            example_demo_btn = gr.Button("填入业务接口示例")
                            rag_checkbox = gr.Checkbox(label="启用 RAG 知识库增强", value=True)
                        with gr.Row():
                            gen_btn = gr.Button("① 生成用例", variant="primary")
                            run_btn = gr.Button("② 执行测试")
                            analyze_btn = gr.Button("③ 失败归因")
                        gen_status = gr.Markdown()
                    with gr.Column(scale=2):
                        case_table = gr.Dataframe(headers=CASE_HEADERS, label="生成的测试用例",
                                                  interactive=False, wrap=True)
                        stats_json = gr.JSON(label="执行统计")
                        report_file = gr.File(label="HTML 测试报告（执行后生成，可下载）")
                        analysis_md = gr.Markdown(label="失败归因分析")
                example_btn.click(fill_example, outputs=doc_input)
                example_demo_btn.click(fill_example_demo, outputs=doc_input)
                gen_btn.click(generate, inputs=[doc_input, rag_checkbox, state],
                              outputs=[case_table, state, gen_status])
                run_btn.click(execute, inputs=state, outputs=[stats_json, report_file, state])
                analyze_btn.click(analyze, inputs=state, outputs=analysis_md)
            with gr.Tab("病害识别演示"):
                gr.Markdown("直接体验被测对象：上传葡萄叶片图片，调用 YOLOv8 识别服务返回病害诊断结果")
                with gr.Row():
                    leaf_input = gr.Image(type="filepath", label="上传葡萄叶片图片", height=350)
                    leaf_output = gr.Image(label="已识别图片", height=350, interactive=False)
                recog_btn = gr.Button("开始识别", variant="primary")
                recog_result = gr.Markdown()
                recog_btn.click(recognize_leaf, inputs=leaf_input,
                                outputs=[leaf_output, recog_result])
    return demo


demo = build_app()

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)
