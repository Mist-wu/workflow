# Bilibili 视频总结工作流

适用场景：用户给出 Bilibili 视频链接，希望转录音频、总结内容。

## 0. 准备工作

### 依赖

```bash
# bilibili-api-python + curl_cffi 用于绕过B站风控
uv venv -p 3.12 /tmp/bili_venv
uv pip install --python /tmp/bili_venv/bin/python bilibili-api-python httpx curl_cffi
```

需要 `ffprobe`、`ffmpeg`（macOS: `brew install ffmpeg`）。

### Gemini API Key

```bash
export GEMINI_API_KEY='your_key'
```

## 1. 取视频基础信息

### 方式一（推荐）：使用 bilibili-api-python

```python
from bilibili_api import sync, select_client, Credential
from bilibili_api.video import Video

select_client('curl_cffi')  # 必须！绕过 TLS 指纹检测
cred = Credential()

v = Video(bvid='BVxxxx', credential=cred)
info = sync(v.get_info())

title = info.get('title')
owner = info.get('owner', {}).get('name')
duration = info.get('duration')         # 秒
cid = info.get('cid')
subtitle_list = info.get('subtitle', {}).get('list', [])
pages = info.get('pages', [])
```

### 方式二（备用）：直接 curl + curl_cffi WBI 签名

B 站的公开 API `/x/web-interface/view` 不要求 WBI 签名，可以直接访问：

```bash
curl -L -A 'Mozilla/5.0' -e 'https://www.bilibili.com' --compressed \
  'https://api.bilibili.com/x/web-interface/view?bvid=BVxxxx'
```

> ⚠️ **风控注意**：直接 curl 容易触发 412/799 风控。优先使用 bilibili-api-python + curl_cffi。

重点记录：

- `title`：视频标题
- `owner.name`：UP 主
- `duration`：时长，秒
- `cid`：后续取音视频地址需要
- `pages[0].part`：分 P 标题
- `subtitle.list`：是否有字幕轨
- `ugc_season` / `view_points`：合集或章节信息

## 2. 判断字幕路线

如果 `subtitle.list` 有字幕：

1. 再请求 `/x/player/v2` 获取字幕 URL。
2. 如果匿名接口返回 `need_login_subtitle: true` 或字幕正文为空，就走音频路线。
3. 如果拿到字幕 URL，优先用字幕总结。字幕通常比音频转写更稳定。

如果 `subtitle.list` 为空，直接走音频路线。

> 经验：**绝大多数 UP 主视频没有字幕**，大概率直接走音频路线。

## 3. 下载实际音频

### 获取 DASH 音频地址并下载

```python
from bilibili_api import sync, select_client, Credential
from bilibili_api.video import Video

select_client('curl_cffi')
cred = Credential()

v = Video(bvid='BVxxxx', credential=cred)
url_data = sync(v.get_download_url(cid=CID))

audio_list = url_data.get('dash', {}).get('audio', [])
best_audio = max(audio_list, key=lambda a: a.get('bandwidth', 0))
audio_url = best_audio['baseUrl']
```

用 curl_cffi（保持 Chrome impersonation）下载：

```bash
python3 -c "
from curl_cffi import requests
r = requests.Session().get('AUDIO_URL', impersonate='chrome')
open('/tmp/BVxxxx_audio.m4s', 'wb').write(r.content)
"
```

或者 Python 代码内直接下载（保持同一个 session，减少风控触发）。

核对实际时长：

```bash
ffprobe -hide_banner -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 /tmp/BVxxxx_audio.m4s
```

> ⚠️ 请求间加 3-5 秒延迟，避免触发频率限制。

## 4. 用音频做内容理解

把音频提交给 Gemini：

```python
import base64, json, os, urllib.request
from pathlib import Path

GEMINI_KEY = os.environ['GEMINI_API_KEY']
audio = Path(f'/tmp/{bvid}_audio.m4s').read_bytes()

prompt = '''请仔细听这段视频音频，输出中文总结：
1. 视频一句话总结。
2. 按时间轴总结主要内容，时间戳到分钟:秒。
3. 提炼核心观点、方法论和可执行建议。
4. 标出容易误解或需要警惕的地方。
不要逐字转录，不要编造音频中没有的信息。'''

payload = {
    'contents': [{
        'parts': [
            {'text': prompt},
            {'inline_data': {'mime_type': 'audio/mp4', 'data': base64.b64encode(audio).decode()}},
        ],
    }],
    'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 4096},
}

url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}'
req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=300) as r:
    resp = json.load(r)

text = '\n'.join(
    part.get('text', '')
    for cand in resp.get('candidates', [])
    for part in cand.get('content', {}).get('parts', [])
)
Path(f'/tmp/{bvid}_summary.md').write_text(text)
```

## 5. 书单类视频的额外校验

书单类视频容易漏书名，要优先做文本源和画面校验。

### 5.1 搜索外部文本源

可搜索：

- 精确标题
- `BVxxxx`
- `UP 主名 + 标题关键词`
- 豆瓣书单、Glasp、YTScribe、Lilys、原作者官网

如果找到豆瓣书单或原文页，用它校对书名，不要只靠音频。

### 5.2 抽视频帧 OCR

当书名出现在画面但音频没有念全时，下载低清视频流：

```python
video_list = url_data.get('dash', {}).get('video', [])
lowest_video = sorted(video_list, key=lambda v: v.get('bandwidth', 0))[0]
video_url = lowest_video['baseUrl']
```

抽拼图帧：

```bash
rm -rf /tmp/bili_frames && mkdir -p /tmp/bili_frames
ffmpeg -hide_banner -loglevel error -i /tmp/BVxxxx_video.m4s \
  -vf "fps=1/3,scale=640:-1,tile=5x4:margin=8:padding=4" \
  -q:v 2 /tmp/bili_frames/sheet_%02d.jpg
```

再用图像模型读取截图中的书名。OCR 结果要人工抽查，低清帧会产生误读。

## 6. 最终输出格式

所有视频总结统一采用以下 markdown 格式：

```markdown
# {视频标题}

- **UP 主：** {UP主名}
- **时长：** {M}:{SS}
- **链接：** [{BVID}](https://www.bilibili.com/video/{BVID})
- **处理方式：** {有/无字幕}，实际音频 + Gemini 2.5 Flash

## 一句话总结
{一句话概括视频核心内容}

## 时间轴摘要
- `MM:SS-MM:SS` {内容要点}
- `MM:SS-MM:SS` {内容要点}

## 核心观点
1. {观点一}
2. {观点二}

## 可执行建议【如适用】
{只有当视频包含可落地的方法论或行动指南时才输出}

## 需要警惕【如适用】
{只有当视频涉及营销话术、争议观点、健康/投资类等需要批判性看待的内容时才输出}
```

### 段落取舍规则

**可执行建议** — 仅当视频包含以下内容时输出：
- 方法论、实操步骤、行动指南
- 可落地的策略或工具推荐

不输出场景：纯观点分享、资讯速递、个人经历叙述、纯分析评论。

**需要警惕** — 仅当视频涉及以下任一时输出：
- 赚钱/投资/商业（营销话术、玄学表达容易被当成事实）
- 健康/心理（非科学主张）
- 有明显争议或偏颇的观点
- 容易以偏概全的结论

不输出场景：纯技术教程、客观知识科普、无争议的信息分享。

### 通用规范
- 标题使用 `#`（一级），段落使用 `##`（二级）
- 信息行使用 `- **key：** value` 列表格式
- 书单/商业类视频的特殊结构可在时间轴摘要中用子段落体现，无需另建模板

## 7. 判断原则

- 永远先核对 B 站实际 `duration` 和 `cid`，不要直接套用外站原片。
- 有字幕优先字幕，无字幕用实际音频。
- 忽略广告，赞助商内容。
- 找到外部转录页时，也要说明来源，并与 B 站时长比对。
- 书单、工具清单、课程清单必须尽量交叉校验。
- 不需要输出长篇逐字稿；用户通常更需要结构化摘要、时间轴和可执行建议。
- 遇到"赚钱、人生、心理、健康"等主题，要单独写"需要警惕"，避免把营销话术或玄学表达当成事实。
- 最终回答里说清楚：无字幕/有字幕、是否下载音频、是否用了外部来源。

## 8. 防坑指南（实战经验）

### 8.1 B 站风控

B 站 API 有严格的反爬机制：

| 错误码 | 含义 | 原因 |
|--------|------|------|
| `-412` | request was banned | TLS 指纹被检测 / 请求频率过高 |
| `-799` | 请求过于频繁 | 频率限制，需等待 10-15s |
| `-352` | 风控校验失败 | WBI 签名错误或密钥过期 |
| 412 HTML 页面 | 安全风控策略 | 需要浏览器环境或 captcha |

**必须使用 `curl_cffi` 的 Chrome impersonation**（`impersonate='chrome'`）来绕过 TLS 指纹检测。普通 `requests`/`curl` 会直接触发 412。

### 8.2 WBI 签名

获取 UP 主视频列表等操作需要 WBI 签名的 API（`/x/space/wbi/arc/search`）：

1. **获取密钥**：从 `/x/web-interface/nav` 响应的 `data.wbi_img` 获取 `img_url` 和 `sub_url`
2. **提取 key**：取 URL 最后一段的文件名（不含扩展名）
3. **计算 mixin key**：用 mixin 表重排列 `img_key + sub_key`，取前 32 字符
4. **签名**：排序参数 + mixin key 的 MD5
5. **请求**：带上 `w_rid` 和 `wts` 参数

密钥是动态的，**不要硬编码 JS bundle 里的 fallback key**，它们会过期。

### 8.3 推荐技术栈

最佳实践组合：

```
bilibili-api-python + curl_cffi (select_client('curl_cffi'))
```

- `bilibili-api-python` 自动处理 WBI 签名和 Cookie 管理
- `curl_cffi` 的 `impersonate='chrome'` 绕过 TLS 指纹检测
- 请求间加至少 3-5 秒延迟
- 批量处理时用 `asyncio` + `await asyncio.sleep(N)`

## 9. 自动化脚本

项目提供了 `script/process.py` 一键处理脚本，自动完成上述所有步骤。

### 用法

```bash
export GEMINI_API_KEY='your_key'

# 传 BVID 或完整 URL
python3 script/process.py BVxxxxxxxx
python3 script/process.py https://www.bilibili.com/video/BVxxxxxxxx
```

### 功能

- 首次运行自动创建 Python 3.12 虚拟环境并安装依赖
- 获取视频信息（标题、UP 主、时长、CID）
- 检测字幕可用性（目前默认走音频路线）
- 下载 DASH 音频流，`baseUrl` 失效自动降级到 `backupUrl`
- `ffprobe` 校验音频完整性
- 调用 Gemini 2.5 Flash 分析音频
- 按统一格式输出到 `video-summaries/` 目录

## 10. 附录

### 10.1 音频下载注意事项

- 音频 DASH 流的 `baseUrl` 有时效性，获取后尽快下载
- 如果 `baseUrl` 过期，可使用 `backupUrl` 备选地址
- 下载时保持 `Referer` 为视频页面地址
- 对 `ffprobe` 结果检查时长，确认下载的是完整音频而非切片
