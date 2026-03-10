import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import request, error
from urllib.parse import unquote
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))
API_KEY = os.environ.get("DEEPSEEK_API_KEY")
API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")
API_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "60"))


def build_prompt(payload):
    pace_map = {
        "intense": "高效冲刺",
        "steady": "稳步推进",
        "relaxed": "轻松打卡"
    }
    pace_value = pace_map.get(payload.get("pace"), payload.get("pace"))
    weekly_review = "开启" if payload.get("review") else "关闭"
    return f"""你是“落地计划生成器”。请根据用户输入生成可执行的日程计划，并严格输出 JSON，禁止任何额外文字。

用户输入：
- 项目/目标：{payload.get("goal")}
- 开始日期：{payload.get("startDate")}
- 周期（天）：{payload.get("duration")}
- 节奏偏好：{pace_value}（仅限：稳步推进 / 高效冲刺 / 轻松打卡）
- 每周复盘：{weekly_review}（仅限：开启 / 关闭）

输出要求（必须遵守）：
1) 输出格式为 JSON：
{{
  "summary": "一句话总结，≤30字",
  "items": [
    {{
      "date": "YYYY-MM-DD",
      "lunar": "YYYY年MM月DD日 农历X月X",
      "tasks": ["任务1", "任务2"],
      "remark": ""
    }}
  ]
}}

2) 计划覆盖从开始日期起连续 {payload.get("duration")} 天，每天一个 items 记录。
3) 每个日期对应 1~2 个核心任务。
4) 每条任务必须是「具体动作 + 可量化标准」，单条任务 ≤ 20 字，适配窄屏日历卡片展示。
5) lunar 必须为该日期准确对应的阴历，格式严格为：YYYY年MM月DD日 农历X月X（示例：2026年03月10日 农历二月初二）。
6) 节奏偏好适配：
   - 稳步推进：每日任务量均衡，难度平稳；
   - 高效冲刺：前期任务密度稍高，后期侧重收尾复盘；
   - 轻松打卡：任务简单易完成，侧重习惯养成，无压力。
7) 每周复盘规则：
   - 若“开启”：每周最后一天（周日）的 remark 标注「本周复盘日」，当日 tasks 仅 1 个轻量任务；
   - 若“关闭”：remark 为空字符串，按节奏正常分配。
8) 禁止空泛话术（如“保持积极心态”“合理安排时间”）。
9) 所有任务必须紧密围绕目标，不得出现无关内容。
10) 只输出 JSON，不得包含解释、Markdown 或其它文字。

现在开始生成。"""


def extract_json(content):
    content = content.strip()
    fence_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", content)
    if fence_match:
        return fence_match.group(1)
    if content.startswith("{") and content.endswith("}"):
        return content
    brace_start = content.find("{")
    brace_end = content.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        return content[brace_start:brace_end + 1]
    return ""


class Handler(BaseHTTPRequestHandler):
    def set_cors(self):
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.set_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.set_cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/deepseek-plan":
            self.send_error(404)
            return
        if not API_KEY:
            self.send_json(500, {"message": "缺少 DEEPSEEK_API_KEY"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            self.send_json(400, {"message": "请求体格式错误"})
            return
        print("payload:", payload)
        prompt = build_prompt(payload)
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一个项目计划生成器"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }).encode("utf-8")
        req = request.Request(API_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {API_KEY}")
        try:
            with request.urlopen(req, timeout=API_TIMEOUT) as resp:
                data = resp.read().decode("utf-8")
        except error.HTTPError as e:
            detail = e.read().decode("utf-8")
            print("deepseek_http_error:", e.code, detail)
            self.send_json(500, {"message": "DeepSeek 调用失败", "detail": detail})
            return
        except Exception as e:
            print("deepseek_error:", str(e))
            self.send_json(500, {"message": "DeepSeek 调用失败", "detail": str(e)})
            return
        try:
            raw_result = json.loads(data)
            content = raw_result.get("choices", [{}])[0].get("message", {}).get("content", "")
            extracted = extract_json(content)
            result = json.loads(extracted)
        except Exception:
            print("deepseek_parse_error:", data)
            self.send_json(500, {"message": "DeepSeek 返回格式错误", "detail": data})
            return
        self.send_json(200, result)

    def do_GET(self):
        url_path = unquote(self.path)
        if url_path == "/":
            self.send_response(200)
            self.set_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        target = os.path.normpath(os.path.join(BASE_DIR, url_path.lstrip("/")))
        if not target.startswith(BASE_DIR):
            self.send_error(403)
            return
        if not os.path.isfile(target):
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(target)
        if not ctype:
            ctype = "application/octet-stream"
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.set_cors()
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        self.send_response(200)
        self.set_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
