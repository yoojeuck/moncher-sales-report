"""판매일보의 게시 경합과 과거 커밋 재실행을 막는 워크플로 계약을 검증한다."""
from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
NAMES = ("daily-scrape.yml", "daily-summarize.yml")


def top_level_mapping(text, name):
    match = re.search(rf"^{re.escape(name)}:\s*\n((?:[ \t]+[^\n]*\n|\n)*)", text, re.MULTILINE)
    if not match:
        return {}
    return dict(re.findall(r"^  ([\w-]+):\s*([^\n]+)$", match.group(1), re.MULTILINE))


class WorkflowPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {name: (WORKFLOWS / name).read_text(encoding="utf-8") for name in NAMES}

    def test_collect_and_summarize_share_one_publication_queue(self):
        groups = {top_level_mapping(text, "concurrency").get("group")
                  for text in self.workflows.values()}
        self.assertEqual(groups, {"sales-data-main"})

    def test_new_runs_do_not_cancel_running_or_pending_collection(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                queue = top_level_mapping(text, "concurrency")
                self.assertEqual(queue.get("cancel-in-progress"), "false")
                self.assertEqual(queue.get("queue"), "max")

    def test_checkout_uses_current_main_after_wait_or_rerun(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                checkouts = re.findall(
                    r"^        uses: actions/checkout@[^\n]+\n((?:[ \t]+[^\n]*\n)*)", text, re.MULTILINE
                )
                self.assertEqual(len(checkouts), 1)
                self.assertRegex(checkouts[0], r"(?m)^        with:\s*\n          ref: main\s*$")

    def test_external_main_changes_cannot_be_force_pushed(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertRegex(text, r"(?m)^          git push\s*$")
                self.assertNotRegex(text, r"(?m)^\s*git\s+(?:push\s+.*--force|rebase|reset\s+--hard)\b")


if __name__ == "__main__":
    unittest.main()
