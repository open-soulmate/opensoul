import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    content: str
    index: int
    start_char: int
    end_char: int
    metadata: dict = field(default_factory=dict)


def chunk_text(text: str, max_chars: int = 800, overlap_chars: int = 50) -> list[Chunk]:
    """Split text into overlapping chunks by character count.

    Each chunk targets 500-1000 characters (default 800) with 50-char overlap.
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chars:
        return [Chunk(content=text, index=0, start_char=0, end_char=len(text))]

    chunks: list[Chunk] = []
    start = 0
    idx = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        # Try to break at a sentence or paragraph boundary
        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + max_chars // 3:
                end = para_break + 2
            else:
                # Look for sentence boundary
                for sep in ("。", ".", "!", "?", "\n"):
                    pos = text.rfind(sep, start + max_chars // 2, end)
                    if pos > start:
                        end = pos + 1
                        break

        chunk_text_str = text[start:end].strip()
        if chunk_text_str:
            chunks.append(
                Chunk(
                    content=chunk_text_str,
                    index=idx,
                    start_char=start,
                    end_char=end,
                )
            )
            idx += 1

        # Move forward with overlap
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end  # Prevent infinite loop
        start = next_start

    return chunks


def chunk_by_paragraphs(text: str, max_chars: int = 800, overlap_chars: int = 50) -> list[Chunk]:
    """Split text by paragraphs, merging small ones and splitting large ones."""
    if not text or not text.strip():
        return []

    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0
    char_offset = 0
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            char_offset += 2
            continue

        if current_len + len(para) + 1 > max_chars and current_parts:
            # Flush current chunk
            content = "\n\n".join(current_parts)
            start = char_offset - current_len
            chunks.append(
                Chunk(
                    content=content,
                    index=idx,
                    start_char=start,
                    end_char=char_offset,
                    metadata={"strategy": "paragraph"},
                )
            )
            idx += 1
            current_parts = []
            current_len = 0

        if len(para) > max_chars:
            # Flush what we have first
            if current_parts:
                content = "\n\n".join(current_parts)
                start = char_offset - current_len
                chunks.append(
                    Chunk(
                        content=content,
                        index=idx,
                        start_char=start,
                        end_char=char_offset,
                        metadata={"strategy": "paragraph"},
                    )
                )
                idx += 1
                current_parts = []
                current_len = 0

            # Split the large paragraph
            sub_chunks = chunk_text(para, max_chars=max_chars, overlap_chars=overlap_chars)
            for sc in sub_chunks:
                chunks.append(
                    Chunk(
                        content=sc.content,
                        index=idx,
                        start_char=char_offset + sc.start_char,
                        end_char=char_offset + sc.end_char,
                        metadata={"strategy": "paragraph_split"},
                    )
                )
                idx += 1
        else:
            current_parts.append(para)
            current_len += len(para) + 2  # +2 for \n\n separator

        char_offset += len(para) + 2

    # Flush remaining
    if current_parts:
        content = "\n\n".join(current_parts)
        start = char_offset - current_len
        chunks.append(
            Chunk(
                content=content,
                index=idx,
                start_char=start,
                end_char=char_offset,
                metadata={"strategy": "paragraph"},
            )
        )

    return chunks


def chunk_by_headers(text: str, max_chars: int = 800, overlap_chars: int = 50) -> list[Chunk]:
    """Split text by markdown-style headers (#, ##, ###, etc.)."""
    if not text or not text.strip():
        return []

    # Split at header lines
    parts = re.split(r"(?=^#{1,6}\s)", text, flags=re.MULTILINE)
    chunks: list[Chunk] = []
    char_offset = 0
    idx = 0

    for part in parts:
        part = part.strip()
        if not part:
            char_offset += len(part) + 1
            continue

        # Extract header as section title
        header_match = re.match(r"^(#{1,6})\s+(.+)", part)
        section_title = header_match.group(2).strip() if header_match else ""

        if len(part) <= max_chars:
            chunks.append(
                Chunk(
                    content=part,
                    index=idx,
                    start_char=char_offset,
                    end_char=char_offset + len(part),
                    metadata={"strategy": "header", "section": section_title},
                )
            )
            idx += 1
        else:
            # Split section content further
            sub_chunks = chunk_text(part, max_chars=max_chars, overlap_chars=overlap_chars)
            for sc in sub_chunks:
                chunks.append(
                    Chunk(
                        content=sc.content,
                        index=idx,
                        start_char=char_offset + sc.start_char,
                        end_char=char_offset + sc.end_char,
                        metadata={"strategy": "header_split", "section": section_title},
                    )
                )
                idx += 1

        char_offset += len(part) + 1

    return chunks


def smart_chunk(
    text: str, content_type: str = "text", max_chars: int = 800, overlap_chars: int = 50
) -> list[Chunk]:
    """Choose the best chunking strategy based on content type."""
    if content_type in ("markdown", "text/markdown", "text/x-markdown"):
        header_chunks = chunk_by_headers(text, max_chars=max_chars, overlap_chars=overlap_chars)
        if len(header_chunks) > 1:
            return header_chunks

    paragraph_chunks = chunk_by_paragraphs(text, max_chars=max_chars, overlap_chars=overlap_chars)
    if len(paragraph_chunks) > 1:
        return paragraph_chunks

    return chunk_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
