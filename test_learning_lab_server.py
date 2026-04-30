import json
import tempfile
import unittest
from pathlib import Path

from learning_lab_server import LearningStateBuilder, LearningLogWriter


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

**视频**: sample
""", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_state_reads_current_learning_progress(self):
        builder = LearningStateBuilder(self.ai, self.en, self.state)
        state = builder.build(today="2026-04-29")

        self.assertEqual(state["english"]["completed_sessions"], 1)
        self.assertEqual(state["english"]["last_log_date"], "2026-04-22")
        self.assertEqual(state["english"]["interruption_days"], 7)
        self.assertEqual(state["english"]["current_task"]["kind"], "recovery")
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


if __name__ == "__main__":
    unittest.main()
