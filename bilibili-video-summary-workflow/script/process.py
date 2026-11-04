#!/usr/bin/env python3
"""
Bilibili 视频总结工具

用法:
  export GEMINI_API_KEY='your_key'
  python3 script/process.py BVxxxxxxxx

依赖:
  bilibili-api-python curl_cffi requests (pip install)
  ffmpeg / ffprobe (brew install ffmpeg)
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from bilibili_api import sync, select_client, Credential
from bilibili_api.video import Video

_GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if not _GEMINI_KEY:
    print("ERROR: export GEMINI_API_KEY='your_key'")
    sys.exit(1)


def log(*args, **kw):
    print("[bili]", *args, **kw, flush=True)


# ── Step 1: 获取视频信息 ──────────────────────────────────

def get_video_info(bvid: str) -> dict:
    select_client("curl_cffi")
    v = Video(bvid=bvid, credential=Credential())
    info = sync(v.get_info())
    pages = info.get("pages", [])
    return {
        "title": info.get("title", ""),
        "owner": info.get("owner", {}).get("name", ""),
        "bvid": bvid,
        "duration": info.get("duration", 0),
        "pages": [
            {"cid": p["cid"], "part": p.get("part", ""), "duration": p.get("duration", 0)}
            for p in pages
        ],
        "has_subtitle": bool(info.get("subtitle", {}).get("list", [])),
    }


# ── Step 2: 下载 DASH 音频 ────────────────────────────────

def download_audio(bvid: str, cid: int, output: str) -> float:
    select_client("curl_cffi")
    v = Video(bvid=bvid, credential=Credential())
    url_data = sync(v.get_download_url(cid=cid))
    audio_list = url_data.get("dash", {}).get("audio", [])
    best = max(audio_list, key=lambda a: a.get("bandwidth", 0))

    log(f"  音频: codec={best.get('codecs')}, bandwidth={best.get('bandwidth')}")
    s = requests.Session()
    r = s.get(best["baseUrl"], headers={"Referer": "https://www.bilibili.com"}, timeout=300)
    Path(output).write_bytes(r.content)
    s.close()

    # 检查实际时长
    dur = subprocess.run(
        ["ffprobe", "-hide_banner", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output],
        capture_output=True, text=True,
    )
    actual = float(dur.stdout.strip() or 0)
    log(f"  已下载: {Path(output).name} ({os.path.getsize(output)/1024/1024:.0f}MB, {actual/60:.0f}min)")
    return actual


# ── Step 3: Remux .m4s → .m4a ────────────────────────────

def remux_to_m4a(src: str, dst: str):
    """Gemini File API 需要规范 MIME type, .m4s 会被识别为 octet-stream"""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src, "-c", "copy", dst],
        check=True,
    )


# ── Step 4: 上传音频到 Gemini File API ────────────────────

def upload_to_gemini(filepath: str, display_name: str) -> str:
    """返回 file_uri"""
    url = "https://generativelanguage.googleapis.com/upload/v1beta/files"
    size = os.path.getsize(filepath)
    content = Path(filepath).read_bytes()

    # 4a. 发起上传
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "Content-Type": "application/json",
    }
    meta = {"file": {"display_name": display_name, "mime_type": "audio/mp4"}}
    r = requests.post(f"{url}?key={_GEMINI_KEY}", json=meta, headers=headers, timeout=120)
    upload_url = r.headers["X-Goog-Upload-URL"]

    # 4b. 上传内容
    headers2 = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "upload, finalize",
        "X-Goog-Upload-Offset": "0",
        "Content-Type": "audio/mp4",
        "Content-Length": str(size),
    }
    r2 = requests.post(upload_url, data=content, headers=headers2, timeout=600)
    r2.raise_for_status()
    result = r2.json()
    file_uri = result["file"]["uri"]
    log(f"  上传完成: {display_name} → {file_uri}")
    return file_uri


# ── Step 5: Gemini 生成总结 ───────────────────────────────

PROMPT = """你是一位专业的视频内容分析师。请仔细听这段直播/视频音频，输出中文总结。

请按照以下结构输出：
## 总结
概括视频核心内容。

## 核心内容
列出主要话题和关键信息。

## 可执行建议【如适用】
如果包含可落地的建议、方法、工具或策略。

## 需要警惕【如适用】
如果涉及投资、商业、健康、心理等需要批判性看待的内容。

注意：忽略广告赞助商内容；不要编造信息；结构化分点输出。"""


def summarize_audio(file_uri: str, context: str = "") -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={_GEMINI_KEY}"
    prompt_text = PROMPT
    if context:
        prompt_text = f"{context}\n\n{PROMPT}"

    payload = {
        "contents": [{"parts": [{"text": prompt_text}, {"file_data": {"mime_type": "audio/mp4", "file_uri": file_uri}}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 16384},
    }
    r = requests.post(url, json=payload, timeout=600)
    r.raise_for_status()
    text = "\n".join(
        part.get("text", "")
        for cand in r.json().get("candidates", [])
        for part in cand.get("content", {}).get("parts", [])
    )
    return text


# ── Step 6: 输出 markdown ─────────────────────────────────

def format_duration(seconds: int) -> str:
    h, m = divmod(seconds, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def generate_output(info: dict, summary_text: str) -> str:
    title = info["title"]
    owner = info["owner"]
    dur = format_duration(info["duration"])
    bvid = info["bvid"]
    source = "无字幕，实际音频 + Gemini 2.5 Flash"

    return f"""# {title}

- **UP 主：** {owner}
- **时长：** {dur}
- **链接：** [{bvid}](https://www.bilibili.com/video/{bvid})
- **处理方式：** {source}

{summary_text}
"""


# ── Main ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python3 process.py BVxxxxxxxx")
        sys.exit(1)

    bvid = sys.argv[1].strip()
    tmp = Path("/tmp")
    log(f"开始处理: {bvid}")

    # 1. 获取信息
    info = get_video_info(bvid)
    log(f"标题: {info['title']}")
    log(f"UP主: {info['owner']}, 时长: {info['duration']}s, 分P: {len(info['pages'])}")
    log(f"字幕: {'有' if info['has_subtitle'] else '无'} → {'字幕路线' if info['has_subtitle'] else '音频路线'}")

    if not info["pages"]:
        log("ERROR: 无可用分P")
        sys.exit(1)

    # 2. 下载并总结每个分P
    page_summaries = []
    for i, page in enumerate(info["pages"]):
        cid = page["cid"]
        label = page.get("part") or f"P{i+1}"
        log(f"\n── 分P {i+1}: {label} ──")

        m4s_path = tmp / f"{bvid}_p{i}_audio.m4s"
        m4a_path = tmp / f"{bvid}_p{i}.m4a"

        # 下载音频
        actual_sec = download_audio(bvid, cid, str(m4s_path))

        # remux → m4a (Gemini 要求规范 MIME)
        log("  remux → m4a ...")
        remux_to_m4a(str(m4s_path), str(m4a_path))
        m4s_path.unlink(missing_ok=True)  # 删除 raw m4s

        # 上传到 Gemini
        uri = upload_to_gemini(str(m4a_path), f"{bvid}_p{i}")

        # 生成总结
        context = f"这是视频的第 {i+1} 部分（共 {len(info['pages'])} 部分），分P标题：{label}"
        summary = summarize_audio(uri, context)
        page_summaries.append(summary)
        log(f"  总结: {len(summary)} 字符")

        # 删除临时 m4a
        m4a_path.unlink(missing_ok=True)

        if i < len(info["pages"]) - 1:
            time.sleep(2)  # 避免频率限制

    # 3. 合并输出
    combined = "\n\n---\n\n".join(page_summaries)
    output = generate_output(info, combined)

    out_dir = Path("video-summaries")
    out_dir.mkdir(exist_ok=True)
    safe_title = info["title"].replace("/", "_").replace(":", "：")
    out_path = out_dir / f"{safe_title}.md"
    out_path.write_text(output)

    log(f"\n✓ 总结已保存: {out_path}")


if __name__ == "__main__":
    main()
