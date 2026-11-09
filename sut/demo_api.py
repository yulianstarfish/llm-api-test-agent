"""被测示例系统：基于 FastAPI 的用户注册/登录/查询服务（端口 8001，内存 dict 存储）。

注意：本系统故意保留 2 个缺陷，供“缺陷检出率”对比实验使用，请勿修复：
  Bug1: /register 对 password 只校验最小长度 6，不校验最大长度 32（超长密码会被接受）；
  Bug2: /register 对重复注册的用户名返回 200，而非接口文档约定的 409（仅响应体 code=1001 提示）。
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="被测示例系统 demo_api")

# 内存用户表：{username: password}
USERS = {}


class RegisterReq(BaseModel):
    username: str
    password: str


class LoginReq(BaseModel):
    username: str
    password: str


@app.post("/register")
def register(req: RegisterReq):
    username = (req.username or "").strip()
    password = req.password or ""
    if not (3 <= len(username) <= 20):
        return JSONResponse(status_code=400, content={"code": 1003, "msg": "参数错误：用户名长度需为3-20"})
    if len(password) < 6:
        return JSONResponse(status_code=400, content={"code": 1003, "msg": "参数错误：密码长度需为6-32"})
    # Bug1（故意保留）：此处未校验密码长度上限 32，超长密码会被接受
    if username in USERS:
        # Bug2（故意保留）：文档约定应返回 409，实际错误地返回了 200
        return JSONResponse(status_code=200, content={"code": 1001, "msg": "用户已存在"})
    USERS[username] = password
    return {"code": 0, "msg": "注册成功"}


@app.post("/login")
def login(req: LoginReq):
    username = (req.username or "").strip()
    if username not in USERS or USERS[username] != req.password:
        return JSONResponse(status_code=401, content={"code": 1002, "msg": "用户名或密码错误"})
    return {"code": 0, "msg": "登录成功", "token": f"token-{username}"}


@app.get("/users/{username}")
def get_user(username: str):
    if username not in USERS:
        return JSONResponse(status_code=404, content={"code": 1004, "msg": "用户不存在"})
    # 注意：只返回用户名，不泄露密码
    return {"code": 0, "data": {"username": username}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
