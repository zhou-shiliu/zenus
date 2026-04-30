#!/usr/bin/env python3
"""Local-first Learning Lab server.

Serves the static Zenus site and exposes local-only APIs:
- GET /api/learning-state
- POST /api/learning-log

No external network, no auth, intended for 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VIDEO_LINK_RE = re.compile(r'\[(?P<title>[^\]]+)\]\((?P<url>https?://[^)]+)\)')
YOUTUBE_ID_RE = re.compile(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})|^([a-zA-Z0-9_-]{11})$')

ROOT = Path('/Users/zhouxb/Documents/github')
DEFAULT_AI = ROOT / 'ai-learning'
DEFAULT_ENGLISH = ROOT / 'english-learning'
DEFAULT_STATE = ROOT / 'zenus-os' / 'records'
DEFAULT_SITE = Path(__file__).resolve().parent


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def extract_section(text: str, heading: str) -> str:
    idx = text.find(heading)
    if idx < 0:
        return ''
    rest = text[idx:]
    m = re.search(r'\n---\n', rest[len(heading):])
    if not m:
        return rest.strip()
    return rest[: len(heading) + m.start()].strip()


def last_english_log(log: str) -> tuple[int | None, date | None]:
    matches = list(re.finditer(r'^##\s+(?:Day|Session)\s*(\d+)?[^\n]*[（(](\d{4}-\d{2}-\d{2})[）)]', log, re.M))
    if not matches:
        return None, None
    m = matches[-1]
    num = int(m.group(1)) if m.group(1) else None
    return num, date.fromisoformat(m.group(2))


def completed_sessions_from_readme(readme: str, log: str) -> int:
    m = re.search(r'已完成训练 Session 数\s*\|\s*(\d+)\s*\|', readme)
    if m:
        return int(m.group(1))
    session_nums = [int(x) for x in re.findall(r'^##\s+Session\s*(\d+)', log, re.M)]
    if session_nums:
        return max(session_nums)
    # Existing old log may use Day 3 but README says one completed session. Fallback conservative.
    return 1 if re.search(r'^##\s+Day\s*\d+', log, re.M) else 0


def parse_english_plan_tasks(plan_text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line in plan_text.splitlines():
        m = re.match(r'^\|\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$', line.strip())
        if not m:
            continue
        day = int(m.group(1))
        task_text = m.group(2).strip()
        video_cell = m.group(3).strip()
        video_match = VIDEO_LINK_RE.search(video_cell)
        video = None
        if video_match:
            video = {
                'title': video_match.group('title').strip(),
                'url': video_match.group('url').strip(),
            }
        elif video_cell and video_cell != '—':
            video = {'title': video_cell, 'url': None}
        tasks.append({'day': day, 'task': task_text, 'video': video})
    return tasks


def extract_recent_english_videos(log_text: str, limit: int = 8) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_heading = None
    current_date = None
    for line in log_text.splitlines():
        header = re.match(r'^##\s+(?:Day|Session)\s*(\d+)?[^\n]*[（(](\d{4}-\d{2}-\d{2})[）)]', line)
        if header:
            current_heading = line.replace('##', '').strip()
            current_date = header.group(2)
            continue
        video_match = re.match(r'^\*\*视频\*\*:\s*(.*)$', line.strip())
        if not video_match:
            continue
        payload = video_match.group(1).strip()
        link = VIDEO_LINK_RE.search(payload)
        if link:
            items.append({
                'session': current_heading,
                'date': current_date,
                'title': link.group('title').strip(),
                'url': link.group('url').strip(),
                'source': payload.split('—')[-1].strip() if '—' in payload else '',
            })
        else:
            items.append({
                'session': current_heading,
                'date': current_date,
                'title': payload,
                'url': None,
                'source': payload.split('—')[-1].strip() if '—' in payload else '',
            })
    return items[-limit:][::-1]


def choose_next_english_task(plan_tasks: list[dict[str, Any]], completed_sessions: int, interruption_days: int | None) -> dict[str, Any] | None:
    if not plan_tasks:
        return None
    completed_days = {task['day'] for task in plan_tasks if task['day'] <= completed_sessions + 2}
    if interruption_days is not None and interruption_days >= 7:
        preferred_day = max(completed_sessions + 3, 4)
    else:
        preferred_day = completed_sessions + 3
    for task in plan_tasks:
        if task['day'] >= preferred_day:
            return task
    for task in plan_tasks:
        if task['day'] not in completed_days:
            return task
    return plan_tasks[-1]


def choose_recommended_video(next_task: dict[str, Any] | None, recent_videos: list[dict[str, Any]], interruption_days: int | None) -> dict[str, Any] | None:
    last_video = recent_videos[0] if recent_videos else None
    if interruption_days is not None and interruption_days >= 7:
        if last_video and last_video.get('url'):
            return {
                'strategy': 'recovery_last_video',
                'day': next_task['day'] if next_task else None,
                'task': '先重看上次视频 2-3 分钟，再继续原计划',
                'task_label': 'Recovery Video',
                'title': last_video.get('title'),
                'url': last_video.get('url'),
                'source': last_video.get('source'),
                'note': '恢复期优先复用上次视频，降低重新启动阻力。',
                'next_up': {
                    'day': next_task['day'],
                    'label': 'Next Video',
                    'title': next_task['video']['title'] if next_task and next_task.get('video') else None,
                    'url': next_task['video']['url'] if next_task and next_task.get('video') else None,
                } if next_task else None,
            }
        if next_task and next_task.get('video'):
            return {
                'strategy': 'recovery_next_task',
                'day': next_task['day'],
                'task': next_task['task'],
                'task_label': 'Resume Task',
                'title': next_task['video']['title'],
                'url': next_task['video']['url'],
                'note': '没有历史视频可恢复，直接进入当前计划视频。',
            }
    if interruption_days is not None and interruption_days >= 2 and last_video and last_video.get('url'):
        return {
            'strategy': 'light_recovery_last_video',
            'day': next_task['day'] if next_task else None,
            'task': '先回看上次视频片段，再继续下一个任务',
            'task_label': 'Recovery Clip',
            'title': last_video.get('title'),
            'url': last_video.get('url'),
            'source': last_video.get('source'),
            'note': '短恢复优先回看旧视频，完成热启动。',
            'next_up': {
                'day': next_task['day'],
                'label': 'Next Video',
                'title': next_task['video']['title'] if next_task and next_task.get('video') else None,
                'url': next_task['video']['url'] if next_task and next_task.get('video') else None,
            } if next_task else None,
        }
    if next_task and next_task.get('video'):
        return {
            'strategy': 'next_task_video',
            'day': next_task['day'],
            'task': next_task['task'],
            'task_label': 'Next Video',
            'title': next_task['video']['title'],
            'url': next_task['video']['url'],
            'note': '按当前计划直接推进。',
        }
    return last_video


def choose_daily_speaking_prompt(interruption_days: int | None) -> dict[str, str]:
    if interruption_days is not None and interruption_days >= 7:
        return {
            'title': '低压恢复开口',
            'prompt': 'What did you learn recently, even if only a little?',
            'task': '开口 30 秒，不追求完整，只说出 2-3 句。',
            'goal': '先恢复开口感，别让英语只停留在输入。',
        }
    if interruption_days is not None and interruption_days >= 2:
        return {
            'title': '轻恢复口语',
            'prompt': 'What are you working on now?',
            'task': '用 2-3 句描述你当前在学或在做的事。',
            'goal': '把英语重新接回当前任务语境。',
        }
    return {
        'title': '日常口语保温',
        'prompt': 'Why is this topic interesting or difficult for you?',
        'task': '围绕今天主题说 30-60 秒。',
        'goal': '保持自然表达，不只会复述术语。',
    }


def extract_youtube_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    m = YOUTUBE_ID_RE.search(url_or_id.strip())
    if not m:
        return None
    return m.group(1) or m.group(2)


def transcript_api_available() -> bool:
    return importlib.util.find_spec('youtube_transcript_api') is not None


def fetch_transcript_lines(video_url: str | None, language_priority: list[str] | None = None, max_lines: int = 12) -> list[str]:
    video_id = extract_youtube_id(video_url)
    if not video_id or not transcript_api_available():
        return []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        segments = api.fetch(video_id, languages=language_priority or ['en'])
        lines = []
        for seg in segments[:max_lines]:
            text = getattr(seg, 'text', '') or ''
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) >= 30:
                lines.append(text)
        return lines
    except Exception:
        return []


def transcript_fetch_status(video_url: str | None) -> str:
    video_id = extract_youtube_id(video_url)
    if not video_id:
        return 'invalid_url'
    if not transcript_api_available():
        return 'dependency_missing'
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        segs = list(api.fetch(video_id, languages=['en']))
        return 'ok' if segs else 'empty'
    except Exception as e:
        msg = str(e).lower()
        if 'ip' in msg and 'blocked' in msg:
            return 'ip_blocked'
        if 'no transcript' in msg:
            return 'no_transcript'
        return 'error'


def choose_shadowing_sentence(transcript_lines: list[str], fallback: str) -> str:
    for line in transcript_lines:
        if 40 <= len(line) <= 140:
            return line
    return fallback


def evaluate_transcript_candidate(video: dict[str, Any] | None) -> dict[str, Any] | None:
    if not video or not video.get('url'):
        return None
    status = transcript_fetch_status(video.get('url'))
    lines = fetch_transcript_lines(video.get('url')) if status == 'ok' else []
    return {
        'title': video.get('title'),
        'url': video.get('url'),
        'day': video.get('day'),
        'task': video.get('task'),
        'source': video.get('source'),
        'transcript_available': bool(lines),
        'transcript_status': status,
        'transcript_lines': lines,
    }


def choose_shadowing_video(primary_video: dict[str, Any] | None, next_up_video: dict[str, Any] | None) -> dict[str, Any] | None:
    primary = evaluate_transcript_candidate(primary_video)
    next_up = evaluate_transcript_candidate(next_up_video)
    if primary and primary.get('transcript_available'):
        primary['reason'] = '当前主推荐视频可直接用于 shadowing。'
        return primary
    if next_up and next_up.get('transcript_available'):
        next_up['reason'] = 'next_up 视频更适合 shadowing，因为 transcript 可用。'
        return next_up
    if primary:
        primary['reason'] = '当前没有检测到 transcript，可先用主推荐视频热启动。'
        return primary
    return next_up


def build_technical_english_pack(ai_stage: str, recommended_video: dict[str, Any] | None) -> dict[str, Any]:
    title = (recommended_video or {}).get('title') or 'this topic'
    video_task = (recommended_video or {}).get('task') or 'Watch the video and explain the main idea.'
    short_topic = ai_stage.split('：', 1)[-1].strip() if '：' in ai_stage else ai_stage.strip()
    fallback_shadow = f'Today I am learning {short_topic}, and this video helps me connect the idea to a bigger system.'
    next_up_video = (recommended_video or {}).get('next_up') if recommended_video else None
    shadowing_video = choose_shadowing_video(recommended_video, next_up_video)
    transcript_lines = (shadowing_video or {}).get('transcript_lines') or []
    shadow = choose_shadowing_sentence(transcript_lines, fallback_shadow)
    output_target = (shadowing_video or {}).get('title') or title
    output_prompt = f'Use 2 English sentences to explain how "{output_target}" relates to {short_topic}.'
    why = (recommended_video or {}).get('note') or f'This video is close enough to {short_topic} to support English input without overloading the main AI study track.'
    return {
        'today_theme': ai_stage,
        'recommended_video': recommended_video,
        'shadowing_video': shadowing_video,
        'recent_videos': [],
        'one_sentence_to_shadow': shadow,
        'next_output_prompt': output_prompt,
        'why_this_video': why,
        'task_hint': video_task,
        'transcript_used': bool(transcript_lines),
        'training_steps': [
            {
                'label': 'Warm-up',
                'instruction': f'先看 1-2 分钟：{title}。目标不是全懂，而是先进入主题。',
            },
            {
                'label': 'Shadowing',
                'instruction': '在浏览器打开英文字幕 / Language Reactor，跟读 1 句，重复 3 遍。',
            },
            {
                'label': 'Output',
                'instruction': output_prompt,
            },
        ],
    }


class LearningStateBuilder:
    def __init__(self, ai_dir: Path = DEFAULT_AI, english_dir: Path = DEFAULT_ENGLISH, state_dir: Path = DEFAULT_STATE):
        self.ai_dir = Path(ai_dir)
        self.english_dir = Path(english_dir)
        self.state_dir = Path(state_dir)

    def build(self, today: str | None = None) -> dict[str, Any]:
        today_date = date.fromisoformat(today) if today else datetime.now().date()
        english_readme = read_text(self.english_dir / 'README.md')
        english_log = read_text(self.english_dir / 'daily-log.md')
        english_plan = read_text(self.english_dir / '90-day-plan.md')
        ai_plan = read_text(self.ai_dir / 'PLAN.md')

        last_num, last_date = last_english_log(english_log)
        completed_sessions = completed_sessions_from_readme(english_readme, english_log)
        interruption_days = (today_date - last_date).days if last_date else None
        plan_tasks = parse_english_plan_tasks(english_plan)
        next_task = choose_next_english_task(plan_tasks, completed_sessions, interruption_days)
        recent_videos = extract_recent_english_videos(english_log)
        recommended_video = choose_recommended_video(next_task, recent_videos, interruption_days)
        daily_speaking = choose_daily_speaking_prompt(interruption_days)

        if interruption_days is not None and interruption_days >= 7:
            current_task = {
                'kind': 'recovery',
                'title': '阶段恢复 Session',
                'actions': ['选一个熟悉的 3-6 分钟技术视频', '查 3 个关键词', '跟读 1 句', '写 2 句英文总结'],
                'after': f"完成后继续：{next_task['video']['title']}" if next_task and next_task.get('video') and next_task['video'].get('title') else '完成后继续下一条英语任务',
            }
        elif interruption_days is not None and interruption_days >= 2:
            current_task = {
                'kind': 'light_recovery',
                'title': '短恢复 Session + 继续下一任务',
                'actions': ['重看上次视频 2-3 分钟', '回顾上一条日志', '写 1-2 句英文总结', f"继续：{next_task['video']['title']}" if next_task and next_task.get('video') and next_task['video'].get('title') else '继续下一条英语任务'],
            }
        else:
            current_task = {
                'kind': 'next_task',
                'title': next_task['video']['title'] if next_task and next_task.get('video') and next_task['video'].get('title') else '继续下一个训练任务',
                'actions': [next_task['task']] if next_task else ['打开 90-day-plan.md 找下一个任务'],
            }

        current_stage = 'Week 5：多元线性回归（p=21~24）'
        m = re.search(r'🔵\s*当前进行中\s*\|\s*([^|]+)\|', ai_plan)
        if m:
            current_stage = m.group(1).strip()

        ai_current = extract_section(ai_plan, '## Week 5 · 多元线性回归（当前阶段）')
        technical_english = build_technical_english_pack(current_stage, recommended_video)
        technical_english['recent_videos'] = recent_videos

        state = {
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'english': {
                'phase': '阶段一：听力重建',
                'completed_sessions': completed_sessions,
                'target_sessions': 60,
                'last_log_number': last_num,
                'last_log_date': last_date.isoformat() if last_date else None,
                'interruption_days': interruption_days,
                'current_task': current_task,
                'technical_english': technical_english,
                'daily_speaking': daily_speaking,
                'recommended_video': recommended_video,
                'recent_videos': recent_videos,
            },
            'ai': {
                'current_stage': current_stage,
                'current_pages': ['p=21', 'p=22', 'p=23', 'p=24'],
                'current_task': '学习多元线性回归 p=21–24，并完成顶层思维复盘笔记',
                'current_section_excerpt': ai_current[:1200],
            },
        }
        return state

    def save(self) -> dict[str, Any]:
        state = self.build()
        write_json(self.state_dir / 'learning-state.json', state)
        return state


class LearningLogWriter:
    def __init__(self, ai_dir: Path = DEFAULT_AI, english_dir: Path = DEFAULT_ENGLISH, state_dir: Path = DEFAULT_STATE):
        self.ai_dir = Path(ai_dir)
        self.english_dir = Path(english_dir)
        self.state_dir = Path(state_dir)

    def write_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        track = payload.get('track', 'English')
        written: list[str] = []
        if track in ('English', 'Both'):
            written.append(str(self._append_english_log(payload)))
            self._update_english_readme_sessions()
        if track in ('AI', 'Both'):
            written.append(str(self._append_ai_log(payload)))
        state = LearningStateBuilder(self.ai_dir, self.english_dir, self.state_dir).save()
        return {'ok': True, 'written_to': written, 'state': state}

    def _append_english_log(self, payload: dict[str, Any]) -> Path:
        path = self.english_dir / 'daily-log.md'
        log = read_text(path)
        completed = completed_sessions_from_readme(read_text(self.english_dir / 'README.md'), log)
        next_session = completed + 1
        entry = self._markdown_entry(payload, heading=f'Session {next_session}')
        path.write_text(log.rstrip() + '\n\n' + entry + '\n', encoding='utf-8')
        return path

    def _append_ai_log(self, payload: dict[str, Any]) -> Path:
        log_dir = self.ai_dir / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{payload.get('date') or datetime.now().date().isoformat()}.md"
        existing = read_text(path)
        entry = self._markdown_entry(payload, heading='AI Learning')
        path.write_text((existing.rstrip() + '\n\n' if existing else '') + entry + '\n', encoding='utf-8')
        return path

    def _update_english_readme_sessions(self) -> None:
        path = self.english_dir / 'README.md'
        readme = read_text(path)
        m = re.search(r'(\| 已完成训练 Session 数 \|\s*)(\d+)(\s*\|\s*60\s*\|)', readme)
        if not m:
            return
        current = int(m.group(2))
        readme = readme[:m.start(2)] + str(current + 1) + readme[m.end(2):]
        path.write_text(readme, encoding='utf-8')

    @staticmethod
    def _markdown_entry(payload: dict[str, Any], heading: str) -> str:
        d = payload.get('date') or datetime.now().date().isoformat()
        return f"""## {heading}（{d}）

**完成模式**：{payload.get('mode') or '-'}

**完成内容**：
{payload.get('done') or '-'}

## 顶层思维复盘

**1. 层级定位**：
{payload.get('level') or '-'}

**2. 核心问题**：
{payload.get('problem') or '-'}

**3. 横向连接**：
{payload.get('connections') or '-'}

**4. 结构沉淀**：
{payload.get('structure') or '-'}

**5. 最终判断：我是在堆东西，还是在建结构？**
{payload.get('judgement') or '-'}

**下一步**：
{payload.get('next') or '-'}
""".strip()


class LearningLabHandler(SimpleHTTPRequestHandler):
    ai_dir = DEFAULT_AI
    english_dir = DEFAULT_ENGLISH
    state_dir = DEFAULT_STATE

    def end_headers(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        if urlparse(self.path).path == '/api/learning-state':
            self._send_json(LearningStateBuilder(self.ai_dir, self.english_dir, self.state_dir).save())
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path == '/api/learning-log':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8')
            try:
                payload = json.loads(raw)
                result = LearningLogWriter(self.ai_dir, self.english_dir, self.state_dir).write_feedback(payload)
                self._send_json(result)
            except Exception as e:
                self._send_json({'ok': False, 'error': str(e)}, status=500)
            return
        self.send_error(404)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description='Run local Learning Lab server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--site-dir', default=str(DEFAULT_SITE))
    args = parser.parse_args()

    import os
    os.chdir(args.site_dir)
    server = ThreadingHTTPServer((args.host, args.port), LearningLabHandler)
    print(f'Learning Lab server on http://{args.host}:{args.port}/learning.html')
    server.serve_forever()


if __name__ == '__main__':
    main()
