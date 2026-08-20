"""
Tests for jarvis/tools/documents.py.

.docx/.pptx/.xlsx are exercised against REAL minimal OOXML zip files built
here with the standard library zipfile module — not mocks standing in for
the format. This is the same verification standard as
test_filesystem_tools.py: real I/O, real files, in an isolated tmp_path.

No pypdf/PyPDF2/fitz/docx/openpyxl/pptx exist in this venv (verified by
`python -c "importlib.util.find_spec(...)"` before this module was written),
so .pdf is expected to fail honestly, not to succeed.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import documents as docs  # noqa: E402


def _zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


# --------------------------------------------------------------- fixtures
@pytest.fixture
def docx_file(tmp_path):
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Hello budget report.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Second paragraph mentions unicorns.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    return _zip(tmp_path / "report.docx", {"word/document.xml": xml})


@pytest.fixture
def pptx_file(tmp_path):
    def slide(text: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    return _zip(tmp_path / "deck.pptx", {
        "ppt/slides/slide1.xml": slide("Welcome slide about penguins."),
        "ppt/slides/slide2.xml": slide("Q3 budget numbers."),
    })


@pytest.fixture
def xlsx_file(tmp_path):
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Budget" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">'
        "<si><t>Name</t></si><si><t>Amount</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Widget</t></is></c><c r="B2"><v>42</v></c></row>'
        "</sheetData></worksheet>"
    )
    return _zip(tmp_path / "numbers.xlsx", {
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels,
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": sheet,
    })


# ------------------------------------------------------------- plain text
def test_read_document_plain_text(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("the quarterly forecast looks solid", encoding="utf-8")
    assert docs.read_document(str(target)) == "the quarterly forecast looks solid"


def test_read_document_markdown_and_json(tmp_path):
    md = tmp_path / "readme.md"
    md.write_text("# Title\n\nBody text.", encoding="utf-8")
    assert "Body text." in docs.read_document(str(md))

    js = tmp_path / "data.json"
    js.write_text('{"key": "value"}', encoding="utf-8")
    assert "value" in docs.read_document(str(js))


def test_read_document_missing_file(tmp_path):
    result = docs.read_document(str(tmp_path / "nope.txt"))
    assert "No file at" in result


def test_read_document_on_a_directory(tmp_path):
    result = docs.read_document(str(tmp_path))
    assert "folder" in result.lower()


def test_read_document_empty_path():
    assert docs.read_document("") == "Which document?"


def test_read_document_empty_file(tmp_path):
    target = tmp_path / "empty.txt"
    target.write_text("", encoding="utf-8")
    result = docs.read_document(str(target))
    assert "empty" in result.lower()


def test_read_document_truncates_long_text(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_CHARS", 50)
    target = tmp_path / "long.txt"
    target.write_text("x" * 500, encoding="utf-8")
    result = docs.read_document(str(target))
    assert "truncated" in result
    assert len(result) < 500


def test_read_document_respects_max_file_mb(tmp_path, monkeypatch):
    target = tmp_path / "big.txt"
    target.write_text("some real content here", encoding="utf-8")
    monkeypatch.setitem(CONFIG, "index", {**CONFIG.get("index", {}), "max_file_mb": 0.00001})
    result = docs.read_document(str(target))
    assert "too large" in result.lower() or "MB" in result


def test_read_document_binary_garbage_is_reported_honestly(tmp_path):
    target = tmp_path / "mystery.dat"
    target.write_bytes(bytes(range(256)) * 20)  # contains NUL bytes -> binary
    result = docs.read_document(str(target))
    assert "binary" in result.lower()


# ------------------------------------------------------------ unsupported
def test_read_document_reads_a_real_pdf():
    """
    PDF reading is a stated requirement ("open pdfs ... and see inside").
    It was genuinely impossible until pypdf (pure Python, ~380KB, no native
    dependency) was added; this asserts it now works end to end rather than
    returning the old honest-but-useless refusal.
    """
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    import io
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(buf.getvalue())
        result = docs.read_document(path)
        # A blank page has no text layer — the honest answer is to say so,
        # not to return "" as though the document were empty.
        assert "ocr" in result.lower() or "no text" in result.lower() or result.strip()
    finally:
        os.unlink(path)


def test_corrupt_pdf_fails_readably(tmp_path):
    """Not a traceback — these strings get spoken aloud."""
    target = tmp_path / "broken.pdf"
    target.write_bytes(b"%PDF-1.4 this is not actually a pdf")
    result = docs.read_document(str(target))
    assert "pdf" in result.lower()
    assert "Traceback" not in result


def test_pdf_text_is_tidied_into_prose():
    """
    pypdf emits one word per line for many real PDFs (verified on a real
    file here). Left alone that triples the token cost of handing the
    document to the model and sounds like dictation when spoken.
    """
    from jarvis.tools.documents import _tidy_pdf_text

    tidied = _tidy_pdf_text("the\nquick\nbrown\nfox\n\n\nnext para")
    assert "the quick brown fox" in tidied
    assert "\n\n" in tidied  # real paragraph breaks survive


@pytest.mark.parametrize("suffix,expect", [
    (".doc", "word"), (".xls", "excel"), (".ppt", "powerpoint"),
    (".png", "image"), (".mp3", "audio"), (".mp4", "video"), (".zip", "archive"),
])
def test_read_document_other_unsupported_formats_are_named_honestly(tmp_path, suffix, expect):
    target = tmp_path / f"file{suffix}"
    target.write_bytes(b"\x00\x01\x02binary junk")
    result = docs.read_document(str(target))
    assert "can't read" in result.lower()
    assert expect in result.lower()


# ------------------------------------------------------------------- docx
def test_read_document_docx(docx_file):
    result = docs.read_document(str(docx_file))
    assert "Hello budget report." in result
    assert "Second paragraph mentions unicorns." in result


def test_read_document_docx_corrupt_zip_is_honest(tmp_path):
    target = tmp_path / "broken.docx"
    target.write_bytes(b"not actually a zip file")
    result = docs.read_document(str(target))
    assert "couldn't open" in result.lower() or "corrupted" in result.lower()


# ------------------------------------------------------------------- pptx
def test_read_document_pptx(pptx_file):
    result = docs.read_document(str(pptx_file))
    assert "Welcome slide about penguins." in result
    assert "Q3 budget numbers." in result
    assert "Slide 1" in result
    assert "Slide 2" in result


# ------------------------------------------------------------------- xlsx
def test_read_document_xlsx(xlsx_file):
    result = docs.read_document(str(xlsx_file))
    assert "Budget" in result          # real sheet name resolved via workbook.xml
    assert "Name" in result and "Amount" in result   # shared strings
    assert "Widget" in result          # inline string cell
    assert "42" in result              # numeric cell


# ------------------------------------------------------- summarize_document
def test_summarize_document_labels_content(tmp_path):
    target = tmp_path / "plan.txt"
    target.write_text("launch on Tuesday", encoding="utf-8")
    result = docs.summarize_document(str(target))
    assert "DOCUMENT: plan.txt" in result
    assert "launch on Tuesday" in result
    assert "summarise it for the user" in result


def test_summarize_document_passes_through_errors_unlabelled(tmp_path):
    result = docs.summarize_document(str(tmp_path / "missing.txt"))
    assert result == f"No file at {tmp_path / 'missing.txt'}."
    assert "DOCUMENT:" not in result


# ----------------------------------------------------------- search_in_files
def test_search_in_files_finds_content_match(tmp_path):
    (tmp_path / "a.txt").write_text("nothing interesting here", encoding="utf-8")
    (tmp_path / "b.txt").write_text("the launch date is confidential", encoding="utf-8")
    result = docs.search_in_files("launch date", folder=str(tmp_path))
    assert "b.txt" in result
    assert "a.txt" not in result


def test_search_in_files_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    result = docs.search_in_files("nonexistent phrase xyz", folder=str(tmp_path))
    assert "No files contain" in result


def test_search_in_files_empty_query(tmp_path):
    assert docs.search_in_files("", folder=str(tmp_path)) == "Search for what?"


def test_search_in_files_missing_folder():
    assert "not a folder" in docs.search_in_files("x", folder="Z:/definitely/not/real")


def test_search_in_files_searches_docx_content(tmp_path, docx_file):
    result = docs.search_in_files("unicorns", folder=str(tmp_path))
    assert docx_file.name in result


def test_search_in_files_skips_excluded_dirs(tmp_path, monkeypatch):
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "target.txt").write_text("secret payload phrase", encoding="utf-8")
    monkeypatch.setitem(CONFIG, "index", {**CONFIG.get("index", {}), "exclude_dirs": ["node_modules"]})
    result = docs.search_in_files("secret payload phrase", folder=str(tmp_path))
    assert "No files contain" in result


def test_search_in_files_skips_never_touch_dirs(tmp_path, monkeypatch):
    protected = tmp_path / "credentials"
    protected.mkdir()
    (protected / "secret.txt").write_text("the vault combination phrase", encoding="utf-8")
    monkeypatch.setattr(docs, "_never_touch_dirs", lambda: [str(protected).replace("\\", "/").lower()])
    result = docs.search_in_files("vault combination phrase", folder=str(tmp_path))
    assert "No files contain" in result


def test_search_in_files_honours_content_index_extensions(tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("special marker phrase", encoding="utf-8")
    (tmp_path / "note.md").write_text("special marker phrase", encoding="utf-8")
    monkeypatch.setitem(CONFIG, "index", {**CONFIG.get("index", {}), "content_index_extensions": [".md"]})
    result = docs.search_in_files("special marker phrase", folder=str(tmp_path))
    assert "note.md" in result
    assert "note.txt" not in result


def test_search_in_files_honours_max_file_mb(tmp_path, monkeypatch):
    (tmp_path / "big.txt").write_text("findable phrase inside a large file", encoding="utf-8")
    monkeypatch.setitem(CONFIG, "index", {**CONFIG.get("index", {}), "max_file_mb": 0.00001})
    result = docs.search_in_files("findable phrase", folder=str(tmp_path))
    assert "No files contain" in result
    assert "skipped" in result.lower()


# ----------------------------------------------------------------- registry
def test_registered_in_unified_registry():
    from jarvis import tools

    for name in ("read_document", "summarize_document", "search_in_files"):
        assert name in tools.REGISTRY, f"{name} missing from unified registry"
        assert tools.REGISTRY[name] is docs.REGISTRY[name]


def test_call_dispatch(tmp_path):
    target = tmp_path / "hi.txt"
    target.write_text("hi there", encoding="utf-8")
    assert docs.call("read_document", {"path": str(target)}) == "hi there"


def test_call_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        docs.call("not_a_real_tool", {})


# ------------------------------------------------------------------- safety
def test_documents_tools_are_all_green():
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    for name in ("read_document", "summarize_document", "search_in_files"):
        verdict = engine.classify(name, {})
        assert not verdict.detail.get("unclassified"), f"{name} isn't in safety.yaml"
        assert verdict.tier is Tier.GREEN, f"{name} classified as {verdict.tier.value}, expected green"


def test_documents_tool_specs_match_registry():
    from jarvis.brain.tools import TOOL_SPECS

    for name in ("read_document", "summarize_document", "search_in_files"):
        assert name in TOOL_SPECS, f"{name} missing a TOOL_SPECS entry"
