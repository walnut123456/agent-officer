from __future__ import annotations

import unittest

from hermes_officer.application.document_chunker import (
    ApproximateTokenCounter,
    ChunkingConfig,
    HierarchicalDocumentChunker,
)


class HierarchicalDocumentChunkerTest(unittest.TestCase):
    def test_should_keep_heading_scope_and_document_metadata(self) -> None:
        text = """<!-- page:12 -->
# 故障诊断
## E23 研磨电机过热

现象：设备停止研磨并显示 E23。

原因：散热口堵塞或电机持续过载。

<!-- page:13 -->
## E24 水路异常

现象：设备显示 E24。
"""
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=80,
            target_tokens=55,
            min_tokens=20,
            overlap_tokens=10,
        )).chunk(text, document_id="doc-1")

        self.assertEqual(2, len(chunks))
        self.assertEqual(["故障诊断", "E23 研磨电机过热"], chunks[0]["section_path"])
        self.assertEqual(12, chunks[0]["page_start"])
        self.assertEqual(["故障诊断", "E24 水路异常"], chunks[1]["section_path"])
        self.assertEqual("doc-1", chunks[1]["document_id"])
        self.assertIn("章节：故障诊断 > E23 研磨电机过热", chunks[0]["text"])
        self.assertEqual(64, len(chunks[0]["content_hash"]))

    def test_should_keep_small_faq_atomic_without_merging(self) -> None:
        text = """# 售后 FAQ

问题：如何重置设备？
答案：长按电源键十秒，听到提示音后松开。

普通补充说明会作为另一个块处理。
"""
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=100,
            target_tokens=80,
            min_tokens=30,
            overlap_tokens=0,
        )).chunk(text)

        self.assertEqual(2, len(chunks))
        self.assertEqual(["faq"], chunks[0]["block_types"])
        self.assertNotIn("普通补充", chunks[0]["text"])
        self.assertIn("普通补充", chunks[1]["text"])

    def test_should_recursively_split_and_never_exceed_hard_limit(self) -> None:
        sentences = [f"第{i}句描述故障现象、原因以及对应处理动作。" for i in range(1, 31)]
        text = "# 维修说明\n\n" + "".join(sentences)
        chunker = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=70,
            target_tokens=50,
            min_tokens=20,
            overlap_tokens=8,
        ))
        chunks = chunker.chunk(text)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(chunk["token_count"] <= 70 for chunk in chunks))
        self.assertTrue(any(chunk["overlap_tokens"] > 0 for chunk in chunks[1:]))
        self.assertTrue(all(chunk["section_path"] == ["维修说明"] for chunk in chunks))

    def test_should_repeat_question_when_long_faq_answer_is_split(self) -> None:
        answer = "。".join(f"处理步骤{i}需要确认状态灯和错误码" for i in range(1, 25))
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=75,
            target_tokens=55,
            min_tokens=20,
            overlap_tokens=0,
        )).chunk(f"问题：设备无法启动怎么办？\n答案：{answer}")

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all("问题：设备无法启动怎么办？" in chunk["text"] for chunk in chunks))
        self.assertTrue(all(chunk["token_count"] <= 75 for chunk in chunks))

    def test_should_repeat_table_header_when_table_is_split(self) -> None:
        rows = "\n".join(f"| E{i:02d} | 故障描述{i}和处理动作{i} |" for i in range(1, 21))
        text = f"""# 错误码

| 编码 | 说明 |
| --- | --- |
{rows}
"""
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=65,
            target_tokens=45,
            min_tokens=15,
            overlap_tokens=0,
        )).chunk(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all("| 编码 | 说明 |" in chunk["text"] for chunk in chunks))
        self.assertTrue(all("| --- | --- |" in chunk["text"] for chunk in chunks))
        self.assertTrue(all(chunk["token_count"] <= 65 for chunk in chunks))

    def test_approximate_token_counter_should_treat_chinese_and_latin_differently(self) -> None:
        counter = ApproximateTokenCounter()
        self.assertEqual(4, counter.count("设备过热"))
        self.assertEqual(2, counter.count("abcdefgh"))


if __name__ == "__main__":
    unittest.main()
