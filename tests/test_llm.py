from __future__ import annotations

import unittest

from app.llm import extract_json_object, heuristic_enrichment


class LLMTests(unittest.TestCase):
    def test_extract_json_from_noisy_output(self) -> None:
        payload = 'Day la ket qua:\n{"summary": "Tom tat.", "topics": ["Kinh tế"], "sentiment": "neutral"}\nHet.'
        data = extract_json_object(payload)
        self.assertEqual(data["summary"], "Tom tat.")
        self.assertEqual(data["topics"], ["Kinh tế"])

    def test_extract_json_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            extract_json_object("khong co json o day")

    def test_heuristic_topics_and_sentiment(self) -> None:
        enrichment = heuristic_enrichment(
            "Chứng khoán tăng mạnh",
            "Thị trường chứng khoán tăng mạnh, nhiều cổ phiếu đạt đỉnh cao. "
            "Nhà đầu tư tích cực giải ngân trong phiên sáng nay.",
        )
        self.assertIn("Kinh tế", enrichment.topics)
        self.assertEqual(enrichment.sentiment, "positive")
        self.assertTrue(enrichment.summary)

    def test_heuristic_default_topic(self) -> None:
        enrichment = heuristic_enrichment("Tieu de", "Mot doan van khong co tu khoa nao dac biet.")
        self.assertEqual(enrichment.topics, ["Tổng hợp"])


if __name__ == "__main__":
    unittest.main()
