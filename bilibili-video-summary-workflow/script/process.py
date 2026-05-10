#!/usr/bin/env python3
"""
Bilibili 视频总结工具

用法:
  python script/process.py BVxxxxxxxx
  python script/process.py https://www.bilibili.com/video/BVxxxxxxxx

环境变量:
  GEMINI_API_KEY  必填
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- 配置 ---
VENV_DIR = Path("/tmp/bili_venv")
PROJECT_DIR = Path(__file__).resolve().parent.parent
SUMMARY_DIR = PROJECT_DIR / "video-summaries"
GEMINI_MODEL = "gemini-2.5-flash"

# Gemini 分析 prompt
ANALYSIS_PROMPT = """请仔细听这段视频音频，输出中文总结，使用以下 markdown 标题（`##`）作为段落分隔：

## 一句话总结

一句话概括视频核心内容。

## 时间轴摘要

按时间顺序列出主要内容，每行以时间戳开头，格式： `MM:SS-MM:SS` 加内容描述。

## 核心观点

提炼 3-5 个核心观点。

## 核心观点

...

## 可执行建议【如适用】

如果视频包含方法论、实操步骤、行动指南等可落地的内容，则列出具体建议。
如果视频仅为观点分享、资讯速递、纯分析评论，则不输出此段落。

## 需要警惕【如适用】

如果视频涉及以下任一情况，则列出需要警惕或容易误解的点：
- 商业/赚钱/投资类内容（营销话术、玄学表达）
- 健康/心理类（非科学主张）
- 有明显争议或偏颇的观点
- 容易以偏概全的结论
- 广告、赞助商内容需要忽略

如果视频为纯粹的技术教程、知识科普、信息分享，则可以不输出此段落。

要求：
- 不要逐字转录
- 不要编造音频中没有的信息
- 只输出上述段落，按实际情况选择是否包含可执行建议和需要警惕"""


def ensure_venv():
    """确保虚拟环境和依赖已就绪"""
    python = VENV_DIR / "bin" / "python"
    if python.exists():
        return str(python)

    print("🔧 创建虚拟环境并安装依赖...", file=sys.stderr)
    subprocess.run(
        ["uv", "venv", "-p", "3.12", str(VENV_DIR)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python),
         "bilibili-api-python", "httpx", "curl_cffi"],
        check=True, capture_output=True,
    )
    return str(python)


def extract_bvid(input_str: str) -> str:
    """从 URL 或直接输入中提取 BVID"""
    m = re.search(r"BV\w{10}", input_str)
    if not m:
        print("错误：无法解析 BVID，请提供 BV 号或完整 URL", file=sys.stderr)
        sys.exit(1)
    return m.group(0)


def run_bili_script(python_bin: str, code: str) -> str:
    """在 venv 内执行 bilibili-api-python 代码，返回 stdout"""
    result = subprocess.run(
        [python_bin, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"bilibili-api 错误: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def get_video_info(python_bin: str, bvid: str) -> dict:
    """获取视频基本信息"""
    code = f"""
from bilibili_api import sync, select_client, Credential
from bilibili_api.video import Video
import json
select_client('curl_cffi')
cred = Credential()
v = Video(bvid='{bvid}', credential=cred)
info = sync(v.get_info())
print(json.dumps({{
    'title': info.get('title'),
    'owner': info.get('owner', {{}}).get('name'),
    'duration': info.get('duration'),
    'cid': info.get('cid'),
    'has_subtitle': bool(info.get('subtitle', {{}}).get('list', [])),
    'pages': info.get('pages', []),
}}, ensure_ascii=False))
"""
    out = run_bili_script(python_bin, code)
    return json.loads(out)


def download_audio(python_bin: str, bvid: str, cid: int, output: Path) -> bool:
    """下载最佳音频流，尝试 baseUrl → backupUrl 降级"""
    code = f"""
from bilibili_api import sync, select_client, Credential
from bilibili_api.video import Video
import json, time

time.sleep(3)
select_client('curl_cffi')
cred = Credential()
v = Video(bvid='{bvid}', credential=cred)
url_data = sync(v.get_download_url(cid={cid}))
audio_list = url_data.get('dash', {{}}).get('audio', [])
if not audio_list:
    print(json.dumps({{'error': 'no audio stream'}}))
    exit(1)
best = max(audio_list, key=lambda a: a.get('bandwidth', 0))
print(json.dumps({{
    'baseUrl': best.get('baseUrl', ''),
    'backupUrl': best.get('backupUrl', []),
}}, ensure_ascii=False))
"""
    out = run_bili_script(python_bin, code)
    data = json.loads(out)

    if "error" in data:
        print(f"错误：{data['error']}", file=sys.stderr)
        return False

    # 收集候选 URL：backupUrl 优先，再 baseUrl
    candidates = list(data.get("backupUrl", [])) + [data["baseUrl"]]

    for i, url in enumerate(candidates):
        if not url:
            continue
        print(f"  尝试下载 (URL #{i + 1})...", file=sys.stderr)
        dl_code = f"""
from curl_cffi import requests
url = {json.dumps(url)}
headers = {{
    'Referer': 'https://www.bilibili.com/video/{bvid}',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
}}
r = requests.Session().get(url, impersonate='chrome', headers=headers, timeout=30)
print(len(r.content))
if len(r.content) > 1000:
    with open('{output}', 'wb') as f:
        f.write(r.content)
else:
    print(0)
"""
        result = subprocess.run(
            [python_bin, "-c", dl_code],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            size = result.stdout.strip()
            if size.isdigit() and int(size) > 1000:
                return True
        time.sleep(2)

    print("错误：所有下载链接均失败", file=sys.stderr)
    return False


def summarize_with_gemini(audio_path: Path) -> str:
    """调用 Gemini API 分析音频"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("错误：请设置 GEMINI_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    audio_data = audio_path.read_bytes()

    payload = {
        "contents": [{
            "parts": [
                {"text": ANALYSIS_PROMPT},
                {"inline_data": {
                    "mime_type": "audio/mp4",
                    "data": base64.b64encode(audio_data).decode(),
                }},
            ],
        }],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    print("  Gemini 分析中（约 30-60 秒）...", file=sys.stderr)
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.load(r)

    parts = []
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            parts.append(part.get("text", ""))
    return "\n".join(parts)


def format_summary(info: dict, bvid: str, summary_text: str) -> str:
    """按统一格式输出总结"""
    duration_min = info["duration"] // 60
    duration_sec = info["duration"] % 60
    subtitle_note = "有字幕" if info["has_subtitle"] else "无字幕"
    return f"""# {info['title']}

- **UP 主：** {info['owner']}
- **时长：** {duration_min}:{duration_sec:02d}
- **链接：** [{bvid}](https://www.bilibili.com/video/{bvid})
- **处理方式：** {subtitle_note}，实际音频 + Gemini 2.5 Flash

{summary_text}
"""


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <BVID 或 URL>", file=sys.stderr)
        sys.exit(1)

    bvid = extract_bvid(sys.argv[1])

    # 1. 确保环境
    python_bin = ensure_venv()

    # 2. 获取视频信息
    print(f"📺 获取视频信息...", file=sys.stderr)
    info = get_video_info(python_bin, bvid)
    print(f"  标题: {info['title']}", file=sys.stderr)
    print(f"  UP主: {info['owner']}", file=sys.stderr)
    print(f"  时长: {info['duration']}s", file=sys.stderr)

    if info["has_subtitle"]:
        print("  有字幕（未实现字幕路线，留待扩展）", file=sys.stderr)

    # 3. 下载音频
    print(f"📥 下载音频...", file=sys.stderr)
    audio_path = Path(f"/tmp/{bvid}_audio.m4s")
    ok = download_audio(python_bin, bvid, info["cid"], audio_path)
    if not ok:
        sys.exit(1)

    # ffprobe 检验
    result = subprocess.run(
        ["ffprobe", "-hide_banner", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, timeout=15,
    )
    audio_dur = result.stdout.strip()
    print(f"  音频时长: {float(audio_dur):.1f}s" if audio_dur else "  音频时长: 未知",
          file=sys.stderr)

    # 4. Gemini 分析
    print(f"🤖 Gemini 分析...", file=sys.stderr)
    summary_text = summarize_with_gemini(audio_path)
    print(f"  ✅ 分析完成", file=sys.stderr)

    # 5. 保存并输出
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', info["title"])
    filename = f"{info['owner']}_{safe_title}.md"
    output_path = SUMMARY_DIR / filename

    formatted = format_summary(info, bvid, summary_text)
    output_path.write_text(formatted)
    print(f"\n💾 已保存: {output_path}", file=sys.stderr)

    # 6. 输出到 stdout
    print(formatted)


if __name__ == "__main__":
    main()
