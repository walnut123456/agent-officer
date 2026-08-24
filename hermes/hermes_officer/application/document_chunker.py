from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum


_PAGE_MARKER = re.compile(r"^\s*<!--\s*page:(\d+)\s*-->\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+|[（(]?[一二三四五六七八九十]+[）)、.]\s*)(.+)$")
_QUESTION = re.compile(r"^\s*(?:Q(?:uestion)?|问题|问)\s*[:：]\s*(.+)$", re.IGNORECASE)
_ANSWER = re.compile(r"^\s*(?:A(?:nswer)?|答案|答)\s*[:：]\s*(.*)$", re.IGNORECASE)
_WARNING = re.compile(r"^\s*(?:注意|警告|提示|备注|Note|Warning)\s*[:：]", re.IGNORECASE)
_TOKEN_PART = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)


class MergePolicy(StrEnum):
    SAME_SECTION = "same_section"
    SAME_GROUP = "same_group"
    ATTACH_PREVIOUS = "attach_previous"
    ISOLATE = "isolate"


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    text: str
    block_type: str
    section_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    group_id: str = ""
    merge_policy: MergePolicy = MergePolicy.SAME_SECTION
    overlap: bool = False


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_tokens: int = 800
    overlap_tokens: int = 80
    min_tokens: int | None = None
    target_tokens: int | None = None

    def normalized(self) -> "ChunkingConfig":
        if self.max_tokens < 50:
            raise ValueError("max_tokens 必须至少为 50")
        overlap = max(0, min(self.overlap_tokens, self.max_tokens // 3))
        minimum = self.min_tokens if self.min_tokens is not None else max(40, int(self.max_tokens * 0.35))
        target = self.target_tokens if self.target_tokens is not None else max(minimum, int(self.max_tokens * 0.70))
        if not 0 <= minimum <= target <= self.max_tokens:
            raise ValueError("必须满足 0 <= min_tokens <= target_tokens <= max_tokens")
        return replace(self, overlap_tokens=overlap, min_tokens=minimum, target_tokens=target)


class ApproximateTokenCounter:
    """Provider-neutral token estimate used for deterministic chunk boundaries.

    Chinese characters and punctuation count as one token. Latin/digit runs use
    four characters per token, which is a conservative approximation for common
    embedding tokenizers. The embedding provider's exact tokenizer can later be
    injected without changing the parser or merge algorithm.
    """

    @staticmethod
    def count(text: str) -> int:
        total = 0
        for match in _TOKEN_PART.finditer(text):
            value = match.group(0)
            if value.isascii() and (value[0].isalnum() or value[0] == "_"):
                total += max(1, math.ceil(len(value) / 4))
            else:
                total += 1
        return total

    def head(self, text: str, budget: int) -> str:
        return self._bounded_slice(text, budget, from_tail=False)

    def tail(self, text: str, budget: int) -> str:
        return self._bounded_slice(text, budget, from_tail=True)

    def _bounded_slice(self, text: str, budget: int, *, from_tail: bool) -> str:
        if budget <= 0 or not text:
            return ""
        if self.count(text) <= budget:
            return text.strip()
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[-middle:] if from_tail else text[:middle]
            if self.count(candidate) <= budget:
                low = middle
            else:
                high = middle - 1
        result = text[-low:] if from_tail else text[:low]
        if from_tail:
            boundaries = list(re.finditer(r"[\n。！？；.!?;]\s*", result))
            if boundaries and boundaries[0].end() < len(result) // 2:
                result = result[boundaries[0].end():]
        else:
            boundaries = list(re.finditer(r"[\n。！？；.!?;]\s*", result))
            if boundaries and boundaries[-1].end() > len(result) // 2:
                result = result[:boundaries[-1].end()]
        return result.strip()


class HierarchicalDocumentChunker:
    """Structure-first parser, recursive splitter, and token-aware merger."""

    # From strongest semantic boundary to weakest fallback boundary.
    _SEPARATORS = (
        re.compile(r"\n\s*\n+"),
        re.compile(r"\n+"),
        re.compile(r"(?<=[。！？!?])\s*"),
        re.compile(r"(?<=[.;；])\s*"),
        re.compile(r"(?<=[：:])\s*"),
        re.compile(r"(?<=[，,])\s*"),
        re.compile(r"\s+"),
    )

    def __init__(
        self,
        config: ChunkingConfig | None = None,
        *,
        token_counter: ApproximateTokenCounter | None = None,
    ) -> None:
        self.config = (config or ChunkingConfig()).normalized()
        self.tokens = token_counter or ApproximateTokenCounter()

    def chunk(self, text: str, *, document_id: str = "") -> list[dict]:
        blocks = self.parse(text)
        atoms = [atom for block in blocks for atom in self._split_block(block)]
        primary = self._merge_atoms(atoms)
        with_overlap = self._apply_overlap(primary)
        result: list[dict] = []
        for index, blocks_in_chunk in enumerate(with_overlap):
            rendered = self._render(blocks_in_chunk)
            pages = [page for block in blocks_in_chunk for page in (block.page_start, block.page_end) if page is not None]
            section_path = next((block.section_path for block in blocks_in_chunk if not block.overlap), ())
            if not section_path:
                section_path = next((block.section_path for block in blocks_in_chunk if block.section_path), ())
            result.append({
                "index": index,
                "chunk_index": index,
                "document_id": document_id,
                "text": rendered,
                "section_path": list(section_path),
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "block_types": list(dict.fromkeys(block.block_type for block in blocks_in_chunk if not block.overlap)),
                "token_count": self.tokens.count(rendered),
                "overlap_tokens": sum(self.tokens.count(block.text) for block in blocks_in_chunk if block.overlap),
                "content_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            })
        return result

    def parse(self, text: str) -> list[DocumentBlock]:
        normalized = re.sub(r"\r\n?", "\n", text).strip()
        if not normalized:
            return []
        lines = normalized.split("\n")
        blocks: list[DocumentBlock] = []
        headings: list[str] = []
        page: int | None = None
        index = 0
        group_sequence = 0

        while index < len(lines):
            line = lines[index].rstrip()
            stripped = line.strip()
            if not stripped:
                index += 1
                continue

            marker = _PAGE_MARKER.match(stripped)
            if marker:
                page = int(marker.group(1))
                index += 1
                continue

            heading = _HEADING.match(stripped)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                headings = headings[: level - 1]
                headings.append(title)
                index += 1
                continue

            if stripped.startswith("```"):
                code_lines = [line]
                index += 1
                while index < len(lines):
                    code_lines.append(lines[index].rstrip())
                    closing = lines[index].strip().startswith("```")
                    index += 1
                    if closing:
                        break
                blocks.append(self._block("\n".join(code_lines), "code", headings, page, MergePolicy.ISOLATE))
                continue

            question = _QUESTION.match(stripped)
            if question:
                faq_lines = [f"问题：{question.group(1).strip()}"]
                cursor = index + 1
                while cursor < len(lines) and not lines[cursor].strip():
                    cursor += 1
                if cursor < len(lines) and (answer := _ANSWER.match(lines[cursor].strip())):
                    faq_lines.append(f"答案：{answer.group(1).strip()}")
                    cursor += 1
                    while cursor < len(lines):
                        following = lines[cursor].strip()
                        if not following:
                            break
                        if _QUESTION.match(following) or _HEADING.match(following) or _PAGE_MARKER.match(following):
                            break
                        faq_lines.append(lines[cursor].rstrip())
                        cursor += 1
                    blocks.append(self._block("\n".join(faq_lines), "faq", headings, page, MergePolicy.ISOLATE))
                    index = cursor
                    continue

            if self._is_table_line(stripped):
                table_lines: list[str] = []
                while index < len(lines) and self._is_table_line(lines[index].strip()):
                    table_lines.append(lines[index].rstrip())
                    index += 1
                blocks.append(self._block("\n".join(table_lines), "table", headings, page, MergePolicy.ISOLATE))
                continue

            if _LIST_ITEM.match(stripped):
                group_sequence += 1
                group_id = f"list-{group_sequence}"
                while index < len(lines):
                    item = _LIST_ITEM.match(lines[index].strip())
                    if not item:
                        break
                    blocks.append(DocumentBlock(
                        text=lines[index].strip(),
                        block_type="list_item",
                        section_path=tuple(headings),
                        page_start=page,
                        page_end=page,
                        group_id=group_id,
                        merge_policy=MergePolicy.SAME_GROUP,
                    ))
                    index += 1
                continue

            paragraph_lines = [line]
            index += 1
            while index < len(lines):
                following = lines[index].rstrip()
                candidate = following.strip()
                if not candidate:
                    break
                if (
                    _PAGE_MARKER.match(candidate)
                    or _HEADING.match(candidate)
                    or candidate.startswith("```")
                    or _QUESTION.match(candidate)
                    or _LIST_ITEM.match(candidate)
                    or self._is_table_line(candidate)
                ):
                    break
                paragraph_lines.append(following)
                index += 1
            paragraph = "\n".join(paragraph_lines).strip()
            policy = MergePolicy.ATTACH_PREVIOUS if _WARNING.match(paragraph) else MergePolicy.SAME_SECTION
            block_type = "warning" if policy is MergePolicy.ATTACH_PREVIOUS else "paragraph"
            blocks.append(self._block(paragraph, block_type, headings, page, policy))

        return blocks

    @staticmethod
    def _block(
        text: str,
        block_type: str,
        headings: list[str],
        page: int | None,
        policy: MergePolicy,
    ) -> DocumentBlock:
        return DocumentBlock(
            text=text.strip(),
            block_type=block_type,
            section_path=tuple(headings),
            page_start=page,
            page_end=page,
            merge_policy=policy,
        )

    @staticmethod
    def _is_table_line(line: str) -> bool:
        return line.count("|") >= 2

    def _split_block(self, block: DocumentBlock) -> list[DocumentBlock]:
        if self.tokens.count(self._render([block])) <= self.config.max_tokens:
            return [block]
        if block.block_type == "faq":
            return self._split_faq(block)
        if block.block_type == "table":
            return self._split_table(block)
        pieces = self._recursive_split(block.text, 0, self._content_budget(block))
        return [replace(block, text=piece) for piece in pieces if piece.strip()]

    def _split_faq(self, block: DocumentBlock) -> list[DocumentBlock]:
        question, separator, answer = block.text.partition("\n答案：")
        if not separator:
            return [replace(block, text=piece) for piece in self._recursive_split(
                block.text, 0, self._content_budget(block)
            )]
        prefix = f"{question}\n答案："
        content_budget = self._content_budget(block)
        if self.tokens.count(prefix) >= content_budget - 20:
            return [replace(block, text=piece) for piece in self._recursive_split(
                block.text, 0, content_budget
            )]
        budget = content_budget - self.tokens.count(prefix)
        answers = self._recursive_split(answer, 0, budget)
        return [replace(block, text=f"{prefix}\n{piece}".strip()) for piece in answers]

    def _split_table(self, block: DocumentBlock) -> list[DocumentBlock]:
        rows = [row for row in block.text.splitlines() if row.strip()]
        if len(rows) <= 2:
            return [replace(block, text=piece) for piece in self._recursive_split(
                block.text, 1, self._content_budget(block)
            )]
        header_count = 2 if re.fullmatch(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*", rows[1]) else 1
        header = rows[:header_count]
        data_rows = rows[header_count:]
        budget = self._content_budget(block)
        if self.tokens.count("\n".join(header)) >= budget - 20:
            return [replace(block, text=piece) for piece in self._recursive_split(block.text, 1, budget)]
        groups: list[list[str]] = []
        current = list(header)
        for row in data_rows:
            candidate = "\n".join([*current, row])
            if len(current) > header_count and self.tokens.count(candidate) > budget:
                groups.append(current)
                current = [*header, row]
            else:
                current.append(row)
            if self.tokens.count("\n".join(current)) > budget:
                oversized = self._recursive_split(row, 5, max(20, budget - self.tokens.count("\n".join(header))))
                current = list(header)
                groups.extend([[*header, piece] for piece in oversized])
        if len(current) > header_count:
            groups.append(current)
        return [replace(block, text="\n".join(group)) for group in groups]

    def _content_budget(self, block: DocumentBlock) -> int:
        heading_tokens = self.tokens.count(self._heading_prefix(block.section_path))
        return max(20, self.config.max_tokens - heading_tokens)

    def _recursive_split(self, text: str, level: int, budget: int) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        if self.tokens.count(stripped) <= budget:
            return [stripped]
        if level >= len(self._SEPARATORS):
            return self._hard_split(stripped, budget)
        parts = self._split_preserving_delimiter(stripped, self._SEPARATORS[level])
        if len(parts) <= 1:
            return self._recursive_split(stripped, level + 1, budget)
        result: list[str] = []
        for part in parts:
            if self.tokens.count(part) > budget:
                result.extend(self._recursive_split(part, level + 1, budget))
            elif part.strip():
                result.append(part.strip())
        return result

    @staticmethod
    def _split_preserving_delimiter(text: str, separator: re.Pattern[str]) -> list[str]:
        parts: list[str] = []
        start = 0
        for match in separator.finditer(text):
            end = match.end()
            if end > start:
                parts.append(text[start:end])
            start = end
        if start < len(text):
            parts.append(text[start:])
        return [part for part in parts if part.strip()]

    def _hard_split(self, text: str, budget: int) -> list[str]:
        result: list[str] = []
        remaining = text.strip()
        while remaining:
            piece = self.tokens.head(remaining, budget)
            if not piece:
                piece = remaining[0]
            result.append(piece)
            remaining = remaining[len(piece):].strip()
        return result

    def _merge_atoms(self, atoms: list[DocumentBlock]) -> list[list[DocumentBlock]]:
        chunks: list[list[DocumentBlock]] = []
        current: list[DocumentBlock] = []
        target = int(self.config.target_tokens or self.config.max_tokens)
        minimum = int(self.config.min_tokens or 0)

        def flush() -> None:
            nonlocal current
            if current:
                chunks.append(current)
                current = []

        for atom in atoms:
            if atom.merge_policy is MergePolicy.ISOLATE:
                flush()
                chunks.append([atom])
                continue
            if current and not self._can_merge(current[-1], atom):
                flush()
            if not current:
                current = [atom]
                continue
            candidate = [*current, atom]
            candidate_tokens = self.tokens.count(self._render(candidate))
            current_tokens = self.tokens.count(self._render(current))
            if candidate_tokens <= target or (current_tokens < minimum and candidate_tokens <= self.config.max_tokens):
                current.append(atom)
            else:
                flush()
                current = [atom]
        flush()
        return chunks

    @staticmethod
    def _can_merge(previous: DocumentBlock, current: DocumentBlock) -> bool:
        if previous.section_path != current.section_path:
            return False
        if current.merge_policy is MergePolicy.ATTACH_PREVIOUS:
            return True
        if previous.merge_policy is MergePolicy.SAME_GROUP or current.merge_policy is MergePolicy.SAME_GROUP:
            return bool(previous.group_id and previous.group_id == current.group_id)
        return True

    def _apply_overlap(self, chunks: list[list[DocumentBlock]]) -> list[list[DocumentBlock]]:
        if self.config.overlap_tokens <= 0 or len(chunks) < 2:
            return chunks
        result: list[list[DocumentBlock]] = []
        for index, current in enumerate(chunks):
            if index == 0 or not current:
                result.append(current)
                continue
            previous = chunks[index - 1]
            if not previous or previous[-1].section_path != current[0].section_path:
                result.append(current)
                continue
            if previous[-1].merge_policy is MergePolicy.ISOLATE or current[0].merge_policy is MergePolicy.ISOLATE:
                result.append(current)
                continue
            current_tokens = self.tokens.count(self._render(current))
            budget = min(self.config.overlap_tokens, self.config.max_tokens - current_tokens)
            if budget <= 0:
                result.append(current)
                continue
            overlap_blocks: list[DocumentBlock] = []
            for block in reversed(previous):
                block_tokens = self.tokens.count(block.text)
                used = sum(self.tokens.count(item.text) for item in overlap_blocks)
                remaining = budget - used
                if remaining <= 0:
                    break
                if block_tokens <= remaining:
                    overlap_blocks.insert(0, replace(block, overlap=True))
                else:
                    tail = self.tokens.tail(block.text, remaining)
                    if tail:
                        overlap_blocks.insert(0, replace(block, text=tail, overlap=True))
                    break
            result.append([*overlap_blocks, *current])
        return result

    def _render(self, blocks: list[DocumentBlock]) -> str:
        if not blocks:
            return ""
        primary = next((block for block in blocks if not block.overlap), blocks[0])
        prefix = self._heading_prefix(primary.section_path)
        body = "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
        return f"{prefix}\n\n{body}".strip() if prefix else body.strip()

    @staticmethod
    def _heading_prefix(section_path: tuple[str, ...]) -> str:
        return f"章节：{' > '.join(section_path)}" if section_path else ""
