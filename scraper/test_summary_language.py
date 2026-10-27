"""합성 응답으로 한국어 요약·원본 매장 범위·교정 상한·실패 시 기존 자료 보존을 검증한다."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import scrape


POSTS = [
    {"store": "가산점", "content": "합성 자료입니다. 재고와 매장 운영을 점검했습니다."},
    {"store": "COEX", "content": "합성 자료입니다. 결제 내역과 고객 문의를 확인했습니다."},
]
TARGET_DATE = "2026-10-26"


def korean_summary():
    return {
        "overall_summary": "매출은 안정적이며 POS 확인과 ERP 재고 대조가 필요합니다.",
        "store_summaries": {
            "가산점": "원재료 재고를 점검했고 고객 문의를 확인했습니다.",
            "COEX": "결제 내역은 정상이며 포장 자재를 추가 확인해야 합니다.",
        },
        "tasks": [
            {
                "priority": "이번주",
                "store": "COEX",
                "action": "POS 단말기 결제 내역을 점검하고 오류를 확인해 주세요.",
            }
        ],
        "highlights": ["ERP 재고 정리를 완료했고 매장 담당자가 확인했습니다."],
    }


def english_summary():
    return {
        "overall_summary": "Sales remained steady across all stores throughout the day.",
        "store_summaries": {
            "가산점": "The store checked stock and handled customer inquiries.",
            "COEX": "Payment records were normal but packaging stock needs review.",
        },
        "tasks": [
            {
                "priority": "이번주",
                "store": "COEX",
                "action": "Review payment records and restock packaging materials.",
            }
        ],
        "highlights": ["Inventory cleanup was completed and reviewed by the store team."],
    }


def response(summary=None, *, status=200, content=None):
    result = Mock(status_code=status)
    result.raise_for_status.return_value = None
    result.json.return_value = {
        "choices": [{"message": {"content": content if content is not None else json.dumps(summary, ensure_ascii=False)}}]
    }
    if status >= 400:
        result.raise_for_status.side_effect = scrape.requests.exceptions.HTTPError(
            "Synthetic HTTP failure", response=result
        )
    return result


class SummaryLanguageTests(unittest.TestCase):
    def setUp(self):
        # 개별 요청 모킹을 빠뜨린 시험도 실제 HTTP로 빠져나가지 않게 한다.
        for guard in (
            patch.object(scrape, "GROQ_API_KEY", "SYNTHETIC_TEST_KEY"),
            patch("builtins.print"),
            patch.object(
                scrape.requests.sessions.Session,
                "request",
                side_effect=AssertionError("LIVE_HTTP_FORBIDDEN"),
            ),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def test_korean_and_short_proper_nouns_are_valid(self):
        value = korean_summary()
        before = copy.deepcopy(value)
        self.assertTrue(scrape.validate_summary(value, POSTS))
        self.assertEqual(value, before)

    def test_ascii_source_store_names_do_not_make_narrative_english(self):
        value = korean_summary()
        self.assertTrue(scrape.validate_summary(value, POSTS))
        value["tasks"][0]["store"] = "전체"
        self.assertTrue(scrape.validate_summary(value, POSTS))

    def test_english_and_dominant_mixed_english_are_rejected_per_field(self):
        self.assertFalse(scrape.validate_summary(english_summary(), POSTS))
        replacements = [
            ("overall_summary", "확인 Sales stayed strong and the team should review inventory."),
            ("store_summary", "매장 The team checked all inventory and completed the customer report."),
            ("task_action", "점검 Review payment records and restock packaging materials."),
            ("highlight", "완료 Inventory cleanup was reviewed and completed by the store team."),
        ]
        for field, text in replacements:
            with self.subTest(field=field):
                value = korean_summary()
                if field == "overall_summary":
                    value[field] = text
                elif field == "store_summary":
                    value["store_summaries"]["가산점"] = text
                elif field == "task_action":
                    value["tasks"][0]["action"] = text
                else:
                    value["highlights"] = [text]
                self.assertFalse(scrape.validate_summary(value, POSTS))

    def test_schema_must_match_exact_keys_and_field_types(self):
        malformed = []
        for key in korean_summary():
            value = korean_summary()
            del value[key]
            malformed.append(value)
        value = korean_summary()
        value["unexpected"] = "추가 내용"
        malformed.append(value)
        for key, bad in [
            ("overall_summary", None), ("overall_summary", 1), ("overall_summary", ""),
            ("store_summaries", []), ("tasks", {}), ("highlights", "문자열"),
        ]:
            value = korean_summary()
            value[key] = bad
            malformed.append(value)
        value = korean_summary()
        value["store_summaries"]["가산점"] = 1
        malformed.append(value)
        for change in ("missing_action", "extra_key", "wrong_priority", "wrong_store_type", "empty_action"):
            value = korean_summary()
            task = value["tasks"][0]
            if change == "missing_action":
                del task["action"]
            elif change == "extra_key":
                task["debug"] = True
            elif change == "wrong_priority":
                task["priority"] = "urgent"
            elif change == "wrong_store_type":
                task["store"] = 1
            else:
                task["action"] = ""
            malformed.append(value)
        value = korean_summary()
        value["highlights"] = [None]
        malformed.append(value)
        for value in [None, [], "문자열", *malformed]:
            with self.subTest(value=value):
                self.assertFalse(scrape.validate_summary(value, POSTS))

    def test_store_names_are_limited_to_exact_source_names_or_all(self):
        for store in ("새로 지어낸 매장", "가산"):
            for field in ("summary", "task"):
                with self.subTest(store=store, field=field):
                    value = korean_summary()
                    if field == "summary":
                        value["store_summaries"][store] = value["store_summaries"].pop("가산점")
                    else:
                        value["tasks"][0]["store"] = store
                    self.assertFalse(scrape.validate_summary(value, POSTS))

    def test_valid_korean_response_uses_one_request(self):
        value = korean_summary()
        posts = copy.deepcopy(POSTS)
        with patch.object(scrape.requests, "post", return_value=response(value)) as post:
            result = scrape.generate_summary(posts, TARGET_DATE)
        self.assertEqual(result, value)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(posts, POSTS)
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertIn("한국어", json.dumps(messages, ensure_ascii=False))

    def test_english_then_korean_corrects_once_and_keeps_source_posts(self):
        value = korean_summary()
        posts = copy.deepcopy(POSTS)
        with patch.object(
            scrape.requests, "post",
            side_effect=[response(english_summary()), response(value)],
        ) as post:
            result = scrape.generate_summary(posts, TARGET_DATE)
        self.assertEqual(result, value)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(posts, POSTS)
        correction_messages = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertIn("한국어", json.dumps(correction_messages, ensure_ascii=False))

    def test_invalid_schema_then_korean_is_also_limited_to_two_requests(self):
        with patch.object(
            scrape.requests, "post",
            side_effect=[response({"overall_summary": "요약만 있는 합성 응답입니다."}), response(korean_summary())],
        ) as post:
            result = scrape.generate_summary(POSTS, TARGET_DATE)
        self.assertEqual(result, korean_summary())
        self.assertEqual(post.call_count, 2)

    def test_always_english_stops_after_two_requests_without_result(self):
        with patch.object(
            scrape.requests, "post",
            side_effect=[response(english_summary()), response(english_summary())],
        ) as post:
            result = scrape.generate_summary(POSTS, TARGET_DATE)
        self.assertEqual(result, {})
        self.assertEqual(post.call_count, 2)

    def test_429_is_not_retried(self):
        with patch.object(scrape.requests, "post", return_value=response(status=429)) as post:
            result = scrape.generate_summary(POSTS, TARGET_DATE)
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 1)

    def test_429_during_correction_does_not_make_a_third_request(self):
        with patch.object(
            scrape.requests, "post",
            side_effect=[response(english_summary()), response(status=429)],
        ) as post:
            result = scrape.generate_summary(POSTS, TARGET_DATE)
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 2)

    def test_network_http_and_api_json_errors_are_not_retried(self):
        malformed_api = response(korean_summary())
        malformed_api.json.side_effect = ValueError("Synthetic API JSON error")
        cases = [
            scrape.requests.exceptions.ConnectionError("Synthetic network error"),
            response(status=503),
            malformed_api,
            response(content="not valid JSON"),
        ]
        for failure in cases:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(scrape.requests, "post", side_effect=[failure]) as post:
                    result = scrape.generate_summary(POSTS, TARGET_DATE)
                self.assertEqual(result, {})
                self.assertEqual(post.call_count, 1)

    def test_missing_api_key_and_empty_posts_do_not_call_http(self):
        with patch.object(scrape.requests, "post") as post:
            with patch.object(scrape, "GROQ_API_KEY", ""):
                self.assertEqual(scrape.generate_summary(POSTS, TARGET_DATE), {})
            self.assertEqual(scrape.generate_summary([], TARGET_DATE), {})
        post.assert_not_called()

    def test_invalid_summary_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory(prefix="sales-language-test-") as directory:
            target = Path(directory) / "summaries.json"
            original = {
                TARGET_DATE: korean_summary(),
                "2026-10-25": {"retained": "기존 합성 자료는 변경하지 않습니다."},
            }
            target.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
            before = target.read_bytes()
            with patch.object(scrape, "SUMMARY_FILE", str(target)):
                for invalid in (english_summary(), {}, None, {"unexpected": "잘못된 형식입니다."}):
                    scrape.save_summary(TARGET_DATE, invalid)
                    self.assertEqual(target.read_bytes(), before)

    def test_invalid_summary_does_not_create_a_new_file(self):
        with tempfile.TemporaryDirectory(prefix="sales-language-test-") as directory:
            target = Path(directory) / "absent" / "summaries.json"
            with patch.object(scrape, "SUMMARY_FILE", str(target)):
                scrape.save_summary(TARGET_DATE, english_summary())
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())

    def test_valid_save_changes_only_requested_date(self):
        with tempfile.TemporaryDirectory(prefix="sales-language-test-") as directory:
            target = Path(directory) / "summaries.json"
            retained = {"retained": "다른 날짜 합성 원본입니다."}
            target.write_text(json.dumps({"2026-10-25": retained}), encoding="utf-8")
            with patch.object(scrape, "SUMMARY_FILE", str(target)):
                scrape.save_summary(TARGET_DATE, korean_summary())
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved[TARGET_DATE], korean_summary())
            self.assertEqual(saved["2026-10-25"], retained)
            self.assertEqual(set(saved), {"2026-10-25", TARGET_DATE})


if __name__ == "__main__":
    unittest.main()

