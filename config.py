"""项目全局配置模块：集中管理 DeepSeek API 参数、模型参数与项目路径常量。"""
import os

from dotenv import load_dotenv

# 从项目根目录的 .env 文件加载环境变量（.env 不进 git，防止 Key 泄露）
load_dotenv()

# ================= DeepSeek 大模型 =================
# 从环境变量 DEEPSEEK_API_KEY 读取；未配置时请在项目根目录创建 .env 文件，写入：
#   DEEPSEEK_API_KEY=sk-你的key
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# DeepSeek 接口地址（兼容 OpenAI SDK 协议）
BASE_URL = "https://api.deepseek.com"
# 使用的对话模型
MODEL = "deepseek-chat"
# 采样温度：越低输出越稳定，测试用例生成场景取 0.3
TEMPERATURE = 0.3
# 调用大模型 / 解析其输出失败时的最大重试次数
MAX_RETRIES = 3
# 单次大模型请求超时时间（秒）
LLM_TIMEOUT = 60

# ================= 项目路径 =================
# 项目根目录（本文件所在目录）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 知识库文档目录（RAG 语料）
KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, "knowledge")
# 生成产物目录（用例 JSON、pytest 脚本、HTML 测试报告）
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated_tests")
# ChromaDB 持久化目录（向量检索模式使用）
CHROMA_DIR = os.path.join(PROJECT_ROOT, "chroma_db")

# ================= 被测系统 =================
# 被测对象1：葡萄叶片病害识别 API（真实 AI 服务）
GRAPE_API_BASE = "http://127.0.0.1:8002"
# 被测对象2：示例业务接口（注册/登录/查询，含 2 个故意缺陷，用于缺陷检出率实验）
DEMO_API_BASE = "http://127.0.0.1:8001"
# 文件上传类接口测试所用的样例图片目录（取自葡萄项目训练集）
# 默认指向本机葡萄项目路径；他人使用时在 .env 中配置 GRAPE_SAMPLE_IMAGE_DIR 覆盖
SAMPLE_IMAGE_PATH = os.getenv(
    "GRAPE_SAMPLE_IMAGE_DIR",
    r"D:\个人\项目\葡萄叶片病害项目\grapes images\train\Grape___Black_rot黑腐病",
)

_client = None


def get_client():
    """惰性创建并缓存 OpenAI 兼容客户端，避免模块导入时初始化任何网络相关对象。"""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=LLM_TIMEOUT)
    return _client
