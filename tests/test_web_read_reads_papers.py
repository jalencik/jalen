"""
web_read could not read an arXiv paper, and for someone who researches machine
learning that is most of the reading.

An arXiv /pdf/ link is application/pdf. Before 00fe7b1 it was decoded as text
and "extracted" into garbage; since then it was refused with "That link is a
PDF". documents._extract_pdf (pypdf, already installed and used by
read_document) reads PDFs fine - it just had no way to get one from the web.

Sizes measured on 2026-09-30 with HEAD requests: BERT 0.7 MB, ResNet 0.8 MB,
Attention 2.1 MB, DeepSeek-R1 4.8 MB, GPT-4 report 5.0 MB, Llama 2 13.0 MB,
Stable Diffusion 39.0 MB (its figures). A PDF cannot be read from a prefix -
its index is at the END - so one past the cap is refused with its size rather
than cut. 25 MB takes six of those seven; the seventh is one he downloads.
"""
from __future__ import annotations

import io

import pytest

from jarvis import taint
from jarvis.tools import research

from test_web_read_is_bounded import _Resp, net  # noqa: F401 - the fixture


def _tiny_pdf(text: str) -> bytes:
    """A real one-page PDF with a text layer, small enough to build by hand."""
    stream = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_an_arxiv_paper_is_read_and_fenced(net):
    net.responses = [_Resp(body=_tiny_pdf("Attention is all you need"), ctype="application/pdf")]
    out = research.web_read("https://arxiv.org/pdf/1706.03762")
    assert "Attention is all you need" in out
    assert "BEGIN UNTRUSTED CONTENT (arxiv.org" in out
    assert taint.origin_now() == "content", "a paper is text a stranger wrote"


def test_a_pdf_bigger_than_the_cap_is_refused_with_its_size_not_cut(net):
    big = _Resp(body=b"%PDF-1.4 small", ctype="application/pdf")
    big.headers["content-length"] = str(39 * 1024 * 1024)
    net.responses = [big]
    out = research.web_read("https://arxiv.org/pdf/2112.10752")
    assert "39" in out and "MB" in out, out
    assert "BEGIN UNTRUSTED CONTENT" not in out


def test_a_pdf_that_streams_past_the_cap_without_saying_its_size_is_refused(net, monkeypatch):
    monkeypatch.setattr(research, "_MAX_PDF_BYTES", 300)
    net.responses = [_Resp(body=_tiny_pdf("x" * 50), ctype="application/pdf")]
    out = research.web_read("https://example.com/paper.pdf")
    assert "too big" in out.lower(), out
    assert "BEGIN UNTRUSTED CONTENT" not in out


def test_the_downloaded_copy_does_not_stay_on_disk(net, monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    net.responses = [_Resp(body=_tiny_pdf("kept nowhere"), ctype="application/pdf")]
    out = research.web_read("https://arxiv.org/pdf/1810.04805")
    assert "kept nowhere" in out
    assert list(tmp_path.iterdir()) == [], "the PDF was left in temp"


def test_a_pdf_with_no_text_layer_is_said_plainly(net):
    blank = _tiny_pdf("").replace(b"() Tj", b"")
    net.responses = [_Resp(body=blank, ctype="application/pdf")]
    out = research.web_read("https://example.com/scan.pdf")
    assert "BEGIN UNTRUSTED CONTENT" not in out
    assert "pdf" in out.lower()


def test_the_cap_is_the_measured_one():
    assert research._MAX_PDF_BYTES == 25 * 1024 * 1024
