import json
import tempfile
import unittest
from pathlib import Path

from learning_lab_server import (
    LearningStateBuilder,
    LearningLogWriter,
    choose_shadowing_sentence,
    choose_shadowing_video,
    extract_youtube_id,
    transcript_fetch_status,
)


class LearningLabServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ai = self.root / "ai-learning"
        self.en = self.root / "english-learning"
        self.state = self.root / "zenus-os" / "records"
        self.ai.mkdir(parents=True)
        self.en.mkdir(parents=True)
        self.state.mkdir(parents=True)
        (self.ai / "PLAN.md").write_text("""# AI 学习计划

## 进度总览

| 状态 | 内容 |
|------|------|
| ✅ 已完成 | Stage 1：AI 全景直觉建立 |
| 🔵 当前进行中 | Week 5：多元线性回归（p=21~24） |

---

## Week 5 · 多元线性回归（当前阶段）

| 周四 | p=21 | 5.1.1 | 多类特征 |
| 周四 | p=22 | 5.2.2 | 向量化 part 1 |
""", encoding="utf-8")
        (self.en / "README.md").write_text("""# English Learning Progress

| 指标 | 当前 | 目标 |
|------|------|------|
| 已完成训练 Session 数 | 1 | 60 |
""", encoding="utf-8")
        (self.en / "daily-log.md").write_text("""# Daily English Log

## Day 3（2026-04-22）

**视频**: [What happens when you type a URL](https://www.youtube.com/watch?v=AlkDbnbv7dk) — ByteByteGo
""", encoding="utf-8")
        (self.en / "90-day-plan.md").write_text("""# 90-Day English Learning Plan for Programmers

|| Day | 任务 | 视频 |
||-----|------|------|
|| 2 | 听力 Fireship；跟读1句 | [How AI Models Are Trained](https://www.youtube.com/watch?v=OBebQ4tLXIM) |
|| 3 | 听力 ByteByteGo；记录3个生词 | [What happens when you type a URL](https://www.youtube.com/watch?v=AlkDbnbv7dk) |
|| 4 | 听力 Fireship；写作：描述今天的工作 | [Neural Networks Explained](https://www.youtube.com/watch?v=oJ7uOj2LRso) |
""", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_state_reads_current_learning_progress(self):
        builder = LearningStateBuilder(self.ai, self.en, self.state)
        state = builder.build(today="2026-04-30")

        self.assertEqual(state["english"]["completed_sessions"], 1)
        self.assertEqual(state["english"]["last_log_date"], "2026-04-22")
        self.assertEqual(state["english"]["interruption_days"], 8)
        self.assertEqual(state["english"]["current_task"]["kind"], "recovery")
        self.assertEqual(state["english"]["recommended_video"]["strategy"], "recovery_last_video")
        self.assertEqual(state["english"]["recommended_video"]["task_label"], "Recovery Video")
        self.assertEqual(state["english"]["recommended_video"]["title"], "What happens when you type a URL")
        self.assertEqual(state["english"]["recommended_video"]["next_up"]["title"], "Neural Networks Explained")
        self.assertEqual(state["english"]["recommended_video"]["next_up"]["label"], "Next Video")
        self.assertEqual(state["english"]["recent_videos"][0]["source"], "ByteByteGo")
        self.assertEqual(state["english"]["technical_english"]["today_theme"], state["ai"]["current_stage"])
        self.assertEqual(state["english"]["technical_english"]["recommended_video"]["strategy"], "recovery_last_video")
        self.assertEqual(state["english"]["technical_english"]["shadowing_video"]["title"], "What happens when you type a URL")
        self.assertFalse(state["english"]["technical_english"]["shadowing_video"]["transcript_available"])
        self.assertIn(state["english"]["technical_english"]["shadowing_video"]["transcript_status"], {"ip_blocked", "no_transcript", "error", "dependency_missing", "empty"})
        self.assertIn("多元线性回归", state["english"]["technical_english"]["one_sentence_to_shadow"])
        self.assertIn("What happens when you type a URL", state["english"]["technical_english"]["next_output_prompt"])
        self.assertIn("恢复期优先复用上次视频", state["english"]["technical_english"]["why_this_video"])
        self.assertFalse(state["english"]["technical_english"]["transcript_used"])
        self.assertEqual(len(state["english"]["technical_english"]["training_steps"]), 3)
        self.assertEqual(state["english"]["technical_english"]["training_steps"][0]["label"], "Warm-up")
        self.assertEqual(state["english"]["technical_english"]["training_steps"][1]["label"], "Shadowing")
        self.assertEqual(state["english"]["technical_english"]["training_steps"][2]["label"], "Output")
        self.assertEqual(state["english"]["daily_speaking"]["title"], "低压恢复开口")
        self.assertIn("多元线性回归", state["ai"]["current_stage"])

    def test_write_english_feedback_appends_log_and_updates_state_file(self):
        writer = LearningLogWriter(self.ai, self.en, self.state)
        payload = {
            "track": "English",
            "date": "2026-04-29",
            "mode": "恢复 Session",
            "done": "看 3 分钟技术视频",
            "level": "表达组织层",
            "problem": "恢复英语输入节奏",
            "connections": "技术解释、短复述",
            "structure": "进入表达库",
            "judgement": "在建结构",
            "next": "继续 Day 4",
        }
        result = writer.write_feedback(payload)

        log = (self.en / "daily-log.md").read_text(encoding="utf-8")
        self.assertIn("## Session 2（2026-04-29）", log)
        self.assertIn("看 3 分钟技术视频", log)
        self.assertTrue((self.state / "learning-state.json").exists())
        saved = json.loads((self.state / "learning-state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["english"]["completed_sessions"], 2)
        self.assertEqual(result["written_to"], [str(self.en / "daily-log.md")])

    def test_extract_youtube_id_and_shadow_fallback(self):
        self.assertEqual(extract_youtube_id("https://www.youtube.com/watch?v=AlkDbnbv7dk"), "AlkDbnbv7dk")
        self.assertEqual(extract_youtube_id("https://youtu.be/oJ7uOj2LRso"), "oJ7uOj2LRso")
        fallback = "Fallback shadow sentence."
        self.assertEqual(choose_shadowing_sentence([], fallback), fallback)
        self.assertEqual(
            choose_shadowing_sentence([
                "too short",
                "This is a transcript line long enough to use as a shadowing sentence for practice.",
            ], fallback),
            "This is a transcript line long enough to use as a shadowing sentence for practice.",
        )
        picked = choose_shadowing_video(
            {"title": "Primary", "url": "https://www.youtube.com/watch?v=AlkDbnbv7dk"},
            {"title": "Next", "url": "https://www.youtube.com/watch?v=oJ7uOj2LRso"},
        )
        self.assertIsNotNone(picked)
        self.assertIn("title", picked)
        self.assertIn("transcript_available", picked)
        self.assertIn(transcript_fetch_status("https://www.youtube.com/watch?v=AlkDbnbv7dk"), {"ok", "ip_blocked", "no_transcript", "error", "dependency_missing", "empty"})


if __name__ == "__main__":
    unittest.main()
