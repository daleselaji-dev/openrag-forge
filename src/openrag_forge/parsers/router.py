from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..domain.models import ParsedBlock


class RouteDecision(dict):
    @property
    def route(self) -> str:
        return str(self["route"])


class ParserRouter:
    """Deterministic, content-aware routing with safe built-in parsers."""

    SUPPORTED = {"txt", "md", "markdown", "html", "htm", "pdf", "docx", "pptx", "xlsx", "csv", "json"}

    def decide(self, filename: str, media_type: str, content: bytes) -> RouteDecision:
        suffix = Path(filename).suffix.lower().lstrip(".")
        if content.startswith(b"%PDF"):
            suffix = "pdf"
        if content.startswith(b"PK") and suffix not in {"docx", "pptx", "xlsx"}:
            suffix = "zip_document"
        if suffix in {"txt", "md", "markdown"}:
            return RouteDecision(route="native_text", confidence=0.99, reason_codes=["text_native"], fallback_route="native_text")
        if suffix in {"html", "htm"} or "html" in media_type:
            return RouteDecision(route="html_structure", confidence=0.98, reason_codes=["html_structure"], fallback_route="native_text")
        if suffix == "pdf":
            has_tables = bool(re.search(rb"table|column|figure|report", content[:200_000], re.I))
            return RouteDecision(route="pdf_layout" if has_tables else "pdf_page_text", confidence=0.90 if has_tables else 0.96, reason_codes=["pdf_has_layout_hints" if has_tables else "pdf_text_baseline"], fallback_route="pdf_page_text")
        if suffix in {"docx", "pptx"}:
            return RouteDecision(route="office_structure", confidence=0.96, reason_codes=[f"{suffix}_xml"], fallback_route="native_text")
        if suffix in {"xlsx", "csv"}:
            return RouteDecision(route="tabular", confidence=0.98, reason_codes=["tabular_rows"], fallback_route="native_text")
        if suffix == "json" or "json" in media_type:
            return RouteDecision(route="json_structure", confidence=0.98, reason_codes=["json_structure"], fallback_route="native_text")
        return RouteDecision(route="native_text", confidence=0.55, reason_codes=["unknown_extension_fallback"], fallback_route="native_text")


def _block(document_id: str, order: int, text: str, block_type: str = "paragraph", **metadata: Any) -> ParsedBlock | None:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return None
    digest = sha256(f"{document_id}:{order}:{clean}".encode()).hexdigest()[:16]
    return ParsedBlock(block_id=f"block:{digest}", document_id=document_id, block_type=block_type, text=clean, order=order, metadata=metadata)


def _native(document_id: str, content: bytes) -> list[ParsedBlock]:
    text = content.decode("utf-8", errors="replace")
    blocks = []
    for order, part in enumerate(re.split(r"\n\s*\n", text)):
        item = _block(document_id, order, part)
        if item:
            blocks.append(item)
    return blocks


def _html(document_id: str, content: bytes) -> list[ParsedBlock]:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    blocks: list[ParsedBlock] = []
    order = 0
    for element in soup.find_all(["h1", "h2", "h3", "p", "li", "table"]):
        item = _block(document_id, order, element.get_text(" ", strip=True), "heading" if element.name.startswith("h") else "table" if element.name == "table" else "paragraph")
        if item:
            blocks.append(item)
            order += 1
    return blocks


def _pdf(document_id: str, content: bytes) -> list[ParsedBlock]:
    reader = PdfReader(io.BytesIO(content))
    blocks = []
    for page_number, page in enumerate(reader.pages, start=1):
        item = _block(document_id, page_number - 1, page.extract_text() or "", "page", page=page_number)
        if item:
            blocks.append(item)
    return blocks


def _office_xml(document_id: str, content: bytes, suffix: str) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".xml") and ("document" in name or "slide" in name or "sheet" in name)]
        order = 0
        for name in names:
            root = ElementTree.fromstring(archive.read(name))
            text = " ".join(node.text or "" for node in root.iter() if node.text)
            item = _block(document_id, order, text, "row" if suffix == "xlsx" else "paragraph", part=name)
            if item:
                blocks.append(item)
                order += 1
    return blocks


def _tabular(document_id: str, content: bytes, suffix: str) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    if suffix == "csv":
        rows = csv.reader(io.StringIO(content.decode("utf-8", errors="replace")))
        for order, row in enumerate(rows):
            item = _block(document_id, order, " | ".join(row), "row", row=order + 1)
            if item:
                blocks.append(item)
        return blocks
    return _office_xml(document_id, content, suffix)


def parse_bytes(document_id: str, filename: str, media_type: str, content: bytes, route: str | None = None) -> tuple[RouteDecision, list[ParsedBlock]]:
    decision = RouteDecision(route=route, confidence=1.0, reason_codes=["user_selected_route"], fallback_route="native_text") if route else ParserRouter().decide(filename, media_type, content)
    suffix = Path(filename).suffix.lower().lstrip(".")
    if decision.route == "native_text":
        blocks = _native(document_id, content)
    elif decision.route == "html_structure":
        blocks = _html(document_id, content)
    elif decision.route in {"pdf_page_text", "pdf_layout"}:
        blocks = _pdf(document_id, content)
    elif decision.route == "office_structure":
        blocks = _office_xml(document_id, content, suffix)
    elif decision.route == "tabular":
        blocks = _tabular(document_id, content, suffix)
    elif decision.route == "json_structure":
        raw = json.loads(content.decode("utf-8", errors="replace"))
        blocks = _native(document_id, json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8"))
    else:
        blocks = _native(document_id, content)
    return decision, blocks

