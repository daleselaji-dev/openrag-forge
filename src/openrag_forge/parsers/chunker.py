from __future__ import annotations

from ..domain.models import Chunk, ParsedBlock


def chunk_blocks(blocks: list[ParsedBlock], max_chars: int = 1200, overlap: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        start = 0
        index = 0
        while start < len(text):
            body = text[start : start + max_chars]
            chunk_id = f"chunk:{block.document_id}:{block.block_id}:{index}"
            chunks.append(Chunk(chunk_id=chunk_id, document_id=block.document_id, text=body, order=len(chunks), block_ids=[block.block_id], metadata={"block_type": block.block_type, "page": block.page, "heading_path": block.heading_path}))
            index += 1
            if start + max_chars >= len(text):
                break
            start += max(1, max_chars - overlap)
    return chunks

