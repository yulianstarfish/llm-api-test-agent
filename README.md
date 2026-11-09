# 基于大语言模型的接口测试用例自动生成系统

毕业设计项目：用户粘贴接口文档 → LLM 自动生成结构化测试用例（RAG 知识库增强）→ 自动转成 pytest 脚本执行 → 输出 HTML 测试报告 → LLM 对失败用例归因。

## 一、安装依赖

本项目统一使用专用虚拟环境（不要往系统主 Python 装任何包）：

```powershell
C:\Users\海星\Documents\Trae-CN\default\bishe_venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 二、配置

打开 `config.py`：`API_KEY` 默认自动复用旧项目（葡萄叶片病害项目）中已填好的 DeepSeek Key；若显示为占位串 `sk-在这里填你的key`，请手动填入你的 DeepSeek API Key。

## 三、运行步骤

1. 安装依赖（见第一节，只需一次）。
2. 启动被测示例系统（新开一个终端，保持运行）：

   ```powershell
   C:\Users\海星\Documents\Trae-CN\default\bishe_venv\Scripts\python.exe sut/demo_api.py
   ```

   服务地址：http://127.0.0.1:8001

3. 启动主系统（再新开一个终端）：

   ```powershell
   C:\Users\海星\Documents\Trae-CN\default\bishe_venv\Scripts\python.exe app.py
   ```

4. 浏览器打开 http://127.0.0.1:7860 ：点击「填入示例」→「① 生成用例」→「② 执行测试」→「③ 失败归因」。

5. 运行对比实验（RAG 开/关各 3 次，自动重启被测系统，结果写入 `experiments/results.csv`）：

   ```powershell
   C:\Users\海星\Documents\Trae-CN\default\bishe_venv\Scripts\python.exe experiments/run_experiment.py
   ```

## 四、项目结构

```
毕设-测试用例生成系统/
├── config.py              # 全局配置：API Key 复用、模型参数、路径常量
├── requirements.txt       # 依赖清单
├── README.md              # 本文件
├── doc_parser.py          # 接口文档解析：文本/JSON/OpenAPI → 结构化 dict
├── kb.py                  # 知识库：Markdown 分块 + jieba/TF-IDF 中文检索（可选 ChromaDB 向量）
├── case_generator.py      # 核心：LLM 生成结构化用例（RAG 增强为实验自变量）
├── script_generator.py    # 用例 → 可运行 pytest 脚本
├── executor.py            # 子进程运行 pytest，产出 HTML 报告并解析结果
├── analyzer.py            # LLM 对失败用例归因（需求理解/接口缺陷/用例错误）
├── app.py                 # Gradio 中文界面入口（127.0.0.1:7860）
├── knowledge/             # RAG 语料：等价类/边界值/场景异常/缺陷案例 4 篇
├── sut/demo_api.py        # 被测示例系统（FastAPI:8001，故意保留 2 个缺陷）
├── generated_tests/       # 生成产物：cases_latest.json / test_generated.py / report.html
└── experiments/           # 对比实验脚本与 results.csv
```

## 五、说明

- `sut/demo_api.py` 故意保留 2 个缺陷（密码不校验长度上限、重复注册返回 200 而非 409），供缺陷检出率实验使用，请勿修复。
- `generated_tests/` 下的脚本与报告均为自动生成，每次“生成用例”会覆盖。
- 知识库默认使用 jieba + TF-IDF 式中文关键词检索；如需 ChromaDB 向量检索，构造 `kb.KnowledgeBase(mode="vector")` 即可。
