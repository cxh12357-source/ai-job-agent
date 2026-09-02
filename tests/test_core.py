from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import boss_ai_assistant as app  # noqa: E402


VALID_RESUME = {
    "name": "测试候选人",
    "education": "测试大学 - 测试专业",
    "target_roles": ["AI Agent工程师", "热管理工程师"],
    "skills": ["Python", "AI Agent"],
    "internships": [
        {
            "company": "示例公司",
            "role": "工程师（实习）",
            "highlights": "使用 Python 和 AI 工作流提升工程分析效率。",
        }
    ],
}


class FakeProvider:
    name = "fake"

    def __init__(self, output_text: str):
        self.output_text = output_text
        self.calls = []

    def generate(self, jd_text, resume_json, *, max_chars=100):
        self.calls.append((jd_text, resume_json, max_chars))
        return self.output_text


class CoreFunctionTests(unittest.TestCase):
    def test_live_marketplace_cli_is_disabled_before_browser_or_resume_access(self):
        args = app.build_parser().parse_args(
            ["--platform", "boss", "--keyword", "工程师", "--resume", "missing.json"]
        )
        with self.assertRaisesRegex(app.SafetyStop, "实时页面模式已停用"):
            app.run(args)

    def test_sanitize_greeting_enforces_100_character_limit(self):
        raw = "招呼语：" + "热" * 120
        result = app.sanitize_greeting(raw)
        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.endswith("…"))
        self.assertFalse(result.startswith("招呼语："))

    def test_sanitize_greeting_collapses_wrappers_and_whitespace(self):
        result = app.sanitize_greeting("```text\n您好，  我擅长 Python。\n``` ")
        self.assertEqual(result, "您好， 我擅长 Python。")

    def test_validate_keyword_rejects_newline_and_empty_value(self):
        with self.assertRaises(ValueError):
            app.validate_keyword("AI\nAgent")
        with self.assertRaises(ValueError):
            app.validate_keyword("   ")
        self.assertEqual(app.validate_keyword("  AI Agent  "), "AI Agent")

    def test_validate_resume_rejects_missing_internship_field(self):
        invalid = dict(VALID_RESUME)
        invalid["internships"] = [{"company": "示例", "role": "实习"}]
        with self.assertRaises(ValueError):
            app.validate_resume(invalid)

    def test_make_job_key_ignores_tracking_query(self):
        first = app.Job(
            platform="boss",
            title="AI工程师",
            company="示例公司",
            url="https://www.zhipin.com/job_detail/ABC123.html?ka=search_list_1",
        )
        second = app.Job(
            platform="boss",
            title="名称发生展示变化",
            company="示例公司（上海）",
            url="https://www.zhipin.com/job_detail/ABC123.html?from=feed",
        )
        self.assertEqual(first.key, second.key)

    def test_make_job_key_normalizes_fallback_identity(self):
        first = app.Job(platform="liepin", title="ＡＩ 工程师", company="示例 公司")
        second = app.Job(platform="liepin", title="AI工程师", company="示例公司")
        self.assertEqual(first.key, second.key)

    def test_generate_greeting_supports_future_provider_interface(self):
        provider = FakeProvider("招呼语：您好，我有 AI Agent 工作流实践，期待沟通。")
        result = app.generate_greeting(
            "负责大模型 Agent 与 LLM 工作流开发，要求 Python 经验。",
            VALID_RESUME,
            provider=provider,
        )
        self.assertEqual(result, "您好，我有 AI Agent 工作流实践，期待沟通。")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][2], 100)

    def test_default_greeting_is_local_and_uses_only_matched_skills(self):
        result = app.generate_greeting(
            "负责 AI Agent 工作流开发，要求 Python 经验。", VALID_RESUME
        )
        self.assertIn("AI Agent", result)
        self.assertIn("Python", result)
        self.assertLessEqual(len(result), 100)

    def test_calculate_local_match_is_explainable(self):
        result = app.calculate_local_match(
            "招聘 AI Agent 工程师，负责 Python 工作流和数据分析。",
            VALID_RESUME,
        )
        self.assertGreaterEqual(result.score, 45)
        self.assertIn("AI Agent", result.matched_skills)
        self.assertIn("Python", result.matched_skills)
        self.assertTrue(result.reasons)
        self.assertTrue(result.evidence)

    def test_calculate_local_match_returns_low_score_without_overlap(self):
        result = app.calculate_local_match(
            "负责法务合同审核与诉讼管理，要求通过法律职业资格考试。",
            VALID_RESUME,
        )
        self.assertEqual(result.score, 0)
        self.assertEqual(result.matched_skills, ())

    def test_ascii_keyword_requires_boundaries(self):
        self.assertFalse(app._contains_term("paid media", "ai"))
        self.assertTrue(app._contains_term("AI 工程师", "ai"))

    def test_write_application_list_sorts_by_score(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "list.json"
            app.write_application_list(
                output,
                [{"title": "低", "score": 30}, {"title": "高", "score": 90}],
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["generator"], "local-rules")
            self.assertEqual(payload["jobs"][0]["title"], "高")

    def test_respectful_delay_stays_in_required_range(self):
        slept = []
        seconds = app.respectful_delay(
            sleeper=slept.append,
            rng=lambda lower, upper: (lower + upper) / 2,
        )
        self.assertEqual(seconds, 7.5)
        self.assertEqual(slept, [7.5])


class InteractionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "interactions.db"
        self.store = app.InteractionStore(self.db_path)
        self.job = app.Job(
            platform="boss",
            title="AI Agent工程师",
            company="示例公司",
            salary="20-30K",
            url="https://www.zhipin.com/job_detail/UNIQUE001.html",
            hr_name="王经理",
            jd="负责 AI Agent 工作流开发，要求 Python 工程能力。",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_is_idempotent(self):
        second = app.InteractionStore(self.db_path)
        self.assertIsNotNone(second)

    def test_draft_does_not_block_but_confirmed_does(self):
        self.assertTrue(self.store.save_draft(self.job, "您好，期待沟通。"))
        self.assertFalse(self.store.should_block(self.job))
        self.assertTrue(
            self.store.reserve_confirmation(self.job, "您好，期待沟通。")
        )
        self.assertTrue(self.store.should_block(self.job))
        self.assertFalse(
            self.store.reserve_confirmation(self.job, "另一条不会覆盖的招呼语。")
        )

    def test_sent_record_cannot_be_overwritten_by_draft(self):
        self.store.save_draft(self.job, "第一版")
        self.store.reserve_confirmation(self.job, "第一版")
        self.store.set_status(self.job, "sent")
        self.assertFalse(self.store.save_draft(self.job, "第二版"))
        row = self.store.get(self.job)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["greeting"], "第一版")


if __name__ == "__main__":
    unittest.main()
