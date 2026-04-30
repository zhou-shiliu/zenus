#!/usr/bin/env python3
"""Local-first Learning Lab server.

Serves the static Zenus site and exposes local-only APIs:
- GET /api/learning-state
- POST /api/learning-log

No external network, no auth, intended for 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


class LearningStateBuilder:
    def __init__(self, ai_dir: Path = DEFAULT_AI, english_dir: Path = DEFAULT_ENGLISH, state_dir: Path = DEFAULT_STATE):
        self.ai_dir = Path(ai_dir)
        self.english_dir = Path(english_dir)
        self.state_dir = Path(state_dir)

    def build(self, today: str | None = None) -> dict[str, Any]:
        today_date = date.fromisoformat(today) if today else datetime.now().date()
        english_readme = read_text(self.english_dir / 'README.md')
        english_log = read_text(self.english_dir / 'daily-log.md')
        ai_plan = read_text(self.ai_dir / 'PLAN.md')

        last_num, last_date = last_english_log(english_log)
        completed_sessions = completed_sessions_from_readme(english_readme, english_log)
        interruption_days = (today_date - last_date).days if last_date else None

        if interruption_days is not None and interruption_days >= 7:
            current_task = {
                'kind': 'recovery',
                'title': '阶段恢复 Session',
                'actions': ['选一个熟悉的 3-6 分钟技术视频', '查 3 个关键词', '跟读 1 句', '写 2 句英文总结'],
                'after': '完成后回到 Day 4 / Neural Networks Explained',
            }
        elif interruption_days is not None and interruption_days >= 2:
            current_task = {
                'kind': 'light_recovery',
                'title': '短恢复 Session + 继续下一任务',
                'actions': ['重看上次视频 2-3 分钟', '回顾上一条日志', '写 1-2 句英文总结', '继续 Day 4 / Neural Networks Explained'],
            }
        else:
            current_task = {
                'kind': 'next_task',
                'title': 'Day 4 / Neural Networks Explained',
                'actions': ['听力 Fireship', '写作：描述今天的工作'],
            }

        current_stage = 'Week 5：多元线性回归（p=21~24）'
        m = re.search(r'🔵\s*当前进行中\s*\|\s*([^|]+)\|', ai_plan)
        if m:
            current_stage = m.group(1).strip()

        ai_current = extract_section(ai_plan, '## Week 5 · 多元线性回归（当前阶段）')

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
