"""被测系统2：葡萄叶片病害识别 REST API（端口 8002）。
将 YOLOv8 分类模型封装为 HTTP 接口，作为毕设系统的"真实 AI 服务被测对象"。
运行环境需装有 ultralytics/torch/fastapi：
C:\\Users\\海星\\AppData\\Local\\Programs\\Python\\Python310\\python.exe
"""
import os
import time
import tempfile

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
import uvicorn

# YOLOv8 分类模型权重（专科毕设训练结果）
# 默认指向本机路径；他人使用时设置环境变量 GRAPE_MODEL_PATH 覆盖（.pt 不进 git）
MODEL_PATH = os.getenv("GRAPE_MODEL_PATH", r"D:\个人\项目\葡萄叶片病害项目\best.pt")

# 模型输出的英文类名 → 中文病害名
CN_NAMES = {
    "Grape___Black_rot": "黑腐病",
    "Grape___Esca_(Black_Measles)": "黑麻疹",
    "Grape___Leaf_blight_(Isariopsis_Leaf_Spot)": "叶枯病",
    "Grape___healthy": "健康",
}

app = FastAPI(title="葡萄叶片病害识别API")
model = None  # 惰性加载，首次请求时载入


def get_model():
    """首次调用时加载模型并缓存，避免重复读盘。"""
    global model
    if model is None:
        from ultralytics import YOLO
        model = YOLO(MODEL_PATH)
    return model


@app.on_event("startup")
def _preload_model():
    """服务启动时预加载模型，避免首次请求因加载耗时而超时。"""
    get_model()


@app.get("/health")
def health():
    """健康检查接口：返回服务与模型加载状态。"""
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/predict")
async def predict(file: UploadFile = File(None)):
    """病害识别接口：上传叶片图片，返回病害类型、置信度与推理耗时。"""
    # 必填参数校验
    if file is None or not file.filename:
        return JSONResponse(status_code=400, content={"error": "仅支持 jpg/jpeg/png 图片"})
    # 文件类型校验
    if not file.filename.lower().endswith((".jpg", ".jpeg", ".png")):
        return JSONResponse(status_code=400, content={"error": "仅支持 jpg/jpeg/png 图片"})

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # 空文件校验（空文件不属于合法图片，按文档约定返回统一错误信息）
        if os.path.getsize(tmp_path) == 0:
            return JSONResponse(status_code=400, content={"error": "仅支持 jpg/jpeg/png 图片"})

        t0 = time.time()
        try:
            results = get_model()(tmp_path, verbose=False)
        except Exception:
            return JSONResponse(status_code=400, content={"error": "仅支持 jpg/jpeg/png 图片"})
        elapsed_ms = round((time.time() - t0) * 1000, 1)

        r = results[0]
        # 分类模型：结果在 r.probs，top1 为最高置信度类别索引
        top1 = int(r.probs.top1)
        conf = round(float(r.probs.top1conf.item()), 4)
        name_en = r.names[top1]
        return {
            "disease_en": name_en,
            "disease_cn": CN_NAMES.get(name_en, name_en),
            "confidence": conf,
            "inference_time_ms": elapsed_ms,
        }
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
