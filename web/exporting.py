# Chat exports as EPUB and PDF. Both take the same message list as the Markdown, text and JSON exports
# (speaker, role, text, time) and return the file as bytes.
#
# EPUB is written by hand with the standard library, so it needs no extra package. PDF uses PyMuPDF's
# story layout, which already ships with the server and handles accented and non-Latin text.
import html
import io
import uuid
import zipfile

import pymupdf


# Escapes a value for the XHTML and HTML that the two formats are built from.
def _esc(value):
    return html.escape(str(value or ""), quote=True)


# One message as a block of HTML: the speaker and time as a heading line, the text as paragraphs.
def _message_html(m):
    stamp = f' <span class="time">{_esc(m["time"])}</span>' if m.get("time") else ""
    paras = "".join(f"<p>{_esc(p)}</p>" for p in str(m.get("text") or "").split("\n") if p.strip()) or "<p></p>"
    return f'<div class="msg"><p class="who"><b>{_esc(m.get("speaker"))}</b>{stamp}</p>{paras}</div>'


# The whole chat as one HTML body, with the title and the export time at the top.
def _body_html(messages, title, when):
    head = f"<h1>{_esc(title)}</h1><p class=\"muted\">Exported {_esc(when)}</p>"
    return head + "".join(_message_html(m) for m in messages)


# A PDF of the chat: A4 pages, margins all round, with the text flowed across as many pages as it needs.
def pdf_bytes(messages, title, when):
    css = ("* { font-family: sans-serif; font-size: 11pt; line-height: 1.4; }"
           " h1 { font-size: 20pt; margin-bottom: 4pt; }"
           " .muted { color: #666666; font-size: 9pt; }"
           " .msg { margin-top: 10pt; }"
           " .who { margin-bottom: 2pt; }"
           " .time { color: #666666; font-size: 9pt; }"
           " p { margin: 0 0 4pt 0; }")
    story = pymupdf.Story(html=_body_html(messages, title, when), user_css=css)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    page = pymupdf.paper_rect("a4")
    where = page + (54, 54, -54, -54)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()


# The content of one EPUB chapter, as XHTML (every tag closed, as EPUB requires).
def _chapter_xhtml(messages, title, when):
    body = _body_html(messages, title, when)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
            f'<head><meta charset="utf-8"/><title>{_esc(title)}</title>\n'
            '<style>body { font-family: serif; } .muted { color: #666666; } .time { color: #666666; font-size: 90%; }'
            ' .msg { margin-top: 1em; } p { margin: 0 0 0.4em 0; }</style></head>\n'
            f'<body>{body}</body>\n</html>\n')


# A navigation document, which EPUB readers require, pointing at the one chapter.
def _nav_xhtml(title):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
            f'<head><meta charset="utf-8"/><title>{_esc(title)}</title></head>\n'
            '<body><nav epub:type="toc" id="toc"><ol>'
            f'<li><a href="chapter.xhtml">{_esc(title)}</a></li>'
            '</ol></nav></body>\n</html>\n')


# The package document: metadata, the manifest of files and the reading order.
def _package_opf(title, when, identifier):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="en">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="bookid">urn:uuid:{identifier}</dc:identifier>'
            f'<dc:title>{_esc(title)}</dc:title>'
            '<dc:language>en</dc:language>'
            f'<meta property="dcterms:modified">{_esc(when)}</meta>'
            '</metadata>\n'
            '<manifest>'
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest>\n'
            '<spine><itemref idref="chapter"/></spine>\n'
            '</package>\n')


# An EPUB 3 file: the mimetype entry comes first and is stored uncompressed, as readers expect.
def epub_bytes(messages, title, when):
    identifier = str(uuid.uuid4())
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>\n')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        book.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/content.opf", _package_opf(title, when, identifier), compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/nav.xhtml", _nav_xhtml(title), compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/chapter.xhtml", _chapter_xhtml(messages, title, when), compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
