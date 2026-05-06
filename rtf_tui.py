#!/usr/bin/env python3
"""rtf-tui — WYSIWYG RTF document editor for the terminal.

Edit rich text (like Word) — RTF source is generated automatically.

Usage:
    python3 rtf_tui.py [file.rtf]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    TextArea,
)
from rich.style import Style
from rich.text import Text

# ═══════════════════════════════════════════════════════════════════════════════
# constants
# ═══════════════════════════════════════════════════════════════════════════════

MENU_DEF: list[tuple[str, list[tuple[str, str]]]] = [
    (" File ", [
        ("New", "new"),
        ("Open...", "open"),
        ("Save", "save"),
        ("Save As...", "save_as"),
        ("---", ""),
        ("Quit", "quit"),
    ]),
    (" Edit ", [
        ("Undo       Ctrl+Z", "undo"),
        ("Redo       Ctrl+Y", "redo"),
        ("Select All Ctrl+A", "select_all"),
    ]),
    (" Format ", [
        ("Bold        Ctrl+B", "insert_bold"),
        ("Italic      Ctrl+I", "insert_italic"),
        ("Underline   Ctrl+U", "insert_underline"),
    ]),
    (" Align ", [
        ("Left", "align_left"),
        ("Center", "align_center"),
        ("Right", "align_right"),
    ]),
    (" View ", [
        ("Toggle RTF Code", "toggle_preview"),
    ]),
    (" Theme ", [
        ("Dracula", "theme:dracula"),
        ("Nord", "theme:nord"),
        ("Gruvbox", "theme:gruvbox"),
        ("Catppuccin", "theme:catppuccin"),
        ("Solarized", "theme:solarized"),
    ]),
]

# ═══════════════════════════════════════════════════════════════════════════════
# Document model
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Span:
    """Contiguous run of text with uniform formatting."""
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = ""
    font_size: int = 24
    font_idx: int = 0

    def copy(self) -> "Span":
        return Span(self.text, self.bold, self.italic, self.underline,
                    self.color, self.font_size, self.font_idx)

    def matches_formatting(self, other: "Span") -> bool:
        return (self.bold == other.bold and self.italic == other.italic
                and self.underline == other.underline
                and self.color == other.color
                and self.font_size == other.font_size
                and self.font_idx == other.font_idx)


@dataclass
class Paragraph:
    """List of spans with an alignment."""
    spans: list[Span] = field(default_factory=list)
    alignment: str = "left"

    @property
    def text_length(self) -> int:
        return sum(len(s.text) for s in self.spans)

    def copy(self) -> "Paragraph":
        return Paragraph([s.copy() for s in self.spans], self.alignment)


@dataclass
class Document:
    """The complete document model."""
    paragraphs: list[Paragraph] = field(default_factory=list)

    def copy(self) -> "Document":
        return Document([p.copy() for p in self.paragraphs])

    @classmethod
    def empty(cls) -> "Document":
        return cls([Paragraph()])


@dataclass
class Cursor:
    """Position: (paragraph index, span index, character offset)."""
    para: int = 0
    span: int = 0
    offset: int = 0

    def copy(self) -> "Cursor":
        return Cursor(self.para, self.span, self.offset)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Cursor):
            return False
        return (self.para == other.para and self.span == other.span
                and self.offset == other.offset)


def _cmp(a: Cursor, b: Cursor) -> int:
    if a.para != b.para:
        return -1 if a.para < b.para else 1
    if a.span != b.span:
        return -1 if a.span < b.span else 1
    if a.offset != b.offset:
        return -1 if a.offset < b.offset else 1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# RTF Generator  (Document → RTF source)
# ═══════════════════════════════════════════════════════════════════════════════

def _escape_rtf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def generate_rtf(doc: Document) -> str:
    lines = [
        "{\\rtf1\\ansi\\deff0",
        "{\\fonttbl{\\f0\\fswiss Arial;}{\\f1\\froman Times New Roman;}"
        "{\\f2\\fmodern Courier New;}}",
        "{\\colortbl;\\red0\\green0\\blue0;\\red255\\green0\\blue0;"
        "\\red0\\green0\\blue255;\\red0\\green128\\blue0;}",
        "\\f0\\fs24",
    ]

    for para in doc.paragraphs:
        align_map = {"left": "ql", "center": "qc", "right": "qr", "justify": "qj"}
        lines.append(f"\\pard\\{align_map.get(para.alignment, 'ql')}")

        for span in para.spans:
            if not span.text:
                continue
            # Emit explicit formatting for every span inside a group {…}.
            # The closing } restores previous state, so no cross-span leakage.
            ctrls = [
                "\\b" if span.bold else "\\b0",
                "\\i" if span.italic else "\\i0",
                "\\ul" if span.underline else "\\ul0",
            ]
            if span.font_size != 24:
                ctrls.append(f"\\fs{span.font_size}")
            if span.font_idx != 0:
                ctrls.append(f"\\f{span.font_idx}")
            if span.color:
                # color table: idx0=auto, idx1=black, idx2=red, idx3=blue, idx4=green
                color_map = {"#ff0000": 2, "#0000ff": 3, "#008000": 4}
                ci = color_map.get(span.color, 0)
                if ci:
                    ctrls.append(f"\\cf{ci}")

            text = _escape_rtf(span.text)
            lines.append("{" + " ".join(ctrls) + " " + text + "}")

        lines.append("\\par")

    lines.append("}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# RTF Parser  (RTF source → Document)
# ═══════════════════════════════════════════════════════════════════════════════

TOKEN_RE = re.compile(r"""
    \\'([0-9a-fA-F]{2})
    | \\u(-?\d+)
    | \\([a-z]+)(-?\d+)?[ ]?
    | (\\\{)
    | (\\\})
    | (\\\r?\n)
    | (\{)
    | (\})
    | (\r?\n)
    | ([^\\{}\r\n]+)
""", re.VERBOSE)


class RTFParser:
    """Parse RTF source into a Document model."""

    def __init__(self) -> None:
        self.color_table: dict[int, str] = {0: ""}
        self._pr = 0
        self._pg = 0
        self._pb = -1
        self._ci = 0

    def parse(self, source: str) -> Document:
        doc = Document()
        stack = [self._default_state()]
        skip = [False]
        buf: list[str] = []

        for m in TOKEN_RE.finditer(source):
            if m.group(1):  # hex escape
                if not skip[-1]:
                    try:
                        buf.append(bytes.fromhex(m.group(1)).decode("windows-1252"))
                    except (ValueError, UnicodeDecodeError):
                        buf.append("?")

            elif m.group(2):  # unicode
                if not skip[-1]:
                    cp = int(m.group(2))
                    if cp < 0:
                        cp += 65536
                    try:
                        buf.append(chr(cp))
                    except (ValueError, OverflowError):
                        buf.append("?")

            elif m.group(3):  # control word
                self._apply(m.group(3),
                            int(m.group(4)) if m.group(4) else None,
                            stack, skip, buf, doc)

            elif m.group(5) or m.group(6):  # \{ or \}
                if not skip[-1]:
                    buf.append("{" if m.group(5) else "}")

            elif m.group(7):  # \<newline>
                pass

            elif m.group(8):  # {
                if buf and not skip[-1]:
                    self._flush(buf, stack[-1], doc)
                stack.append(dict(stack[-1]))
                skip.append(skip[-1])

            elif m.group(9):  # }
                if buf and not skip[-1]:
                    self._flush(buf, stack[-1], doc)
                if len(stack) > 1:
                    stack.pop()
                    skip.pop()

            elif m.group(10):  # plain newline — ignore in RTF
                pass

            elif m.group(11):  # plain text
                if not skip[-1]:
                    buf.append(m.group(11))

        if buf and not skip[-1]:
            self._flush(buf, stack[-1], doc)
        return doc

    @staticmethod
    def _default_state() -> dict:
        return {"bold": False, "italic": False, "underline": False,
                "font_size": 24, "font_idx": 0, "color": "", "alignment": "left"}

    @staticmethod
    def _flush(buf: list[str], state: dict, doc: Document) -> None:
        if not buf:
            return
        text = "".join(buf)
        buf.clear()
        if not doc.paragraphs:
            doc.paragraphs.append(Paragraph(alignment=state["alignment"]))
        para = doc.paragraphs[-1]
        span = Span(text=text, bold=state["bold"], italic=state["italic"],
                    underline=state["underline"], color=state["color"],
                    font_size=state["font_size"], font_idx=state["font_idx"])
        if para.spans and para.spans[-1].matches_formatting(span):
            para.spans[-1].text += span.text
        else:
            para.spans.append(span)

    def _apply(self, word: str, param: int | None, stack: list[dict],
               skip: list[bool], buf: list[str], doc: Document) -> None:
        state = stack[-1]

        if word == "par":
            if buf and not skip[-1]:
                self._flush(buf, state, doc)
            if not doc.paragraphs:
                doc.paragraphs.append(Paragraph(alignment=state["alignment"]))
            doc.paragraphs.append(Paragraph(alignment=state["alignment"]))
        elif word == "pard":
            state["alignment"] = "left"
        elif word == "plain":
            al = state["alignment"]
            state.update(self._default_state())
            state["alignment"] = al

        elif word in ("b", "b0"):
            v = not (word == "b0" or param == 0)
            if state["bold"] != v and buf:
                self._flush(buf, state, doc)
            state["bold"] = v
        elif word in ("i", "i0"):
            v = not (word == "i0" or param == 0)
            if state["italic"] != v and buf:
                self._flush(buf, state, doc)
            state["italic"] = v
        elif word in ("ul", "ul0", "ulnone"):
            v = not (word in ("ul0", "ulnone") or param == 0)
            if state["underline"] != v and buf:
                self._flush(buf, state, doc)
            state["underline"] = v

        elif word in ("qc", "qr", "ql", "qj"):
            m = {"qc": "center", "qr": "right", "ql": "left", "qj": "justify"}
            state["alignment"] = m[word]
            # Update last empty paragraph (created by \par before alignment cmd)
            if doc.paragraphs and not doc.paragraphs[-1].spans:
                doc.paragraphs[-1].alignment = m[word]
        elif word == "fs" and param is not None:
            state["font_size"] = param
        elif word == "f" and param is not None:
            state["font_idx"] = param
        elif word == "cf" and param is not None:
            c = self.color_table.get(param, "")
            if state["color"] != c and buf:
                self._flush(buf, state, doc)
            state["color"] = c
        elif word == "cb":
            pass

        elif word in ("fonttbl", "colortbl", "stylesheet"):
            skip[-1] = True

        elif word == "red" and param is not None:
            self._pr = param
        elif word == "green" and param is not None:
            self._pg = param
        elif word == "blue" and param is not None:
            if param >= 0:
                self._ci += 1
                self.color_table[self._ci] = f"#{self._pr:02x}{self._pg:02x}{param:02x}"


# ═══════════════════════════════════════════════════════════════════════════════
# WYSIWYG Rich Text Editor
# ═══════════════════════════════════════════════════════════════════════════════

class RichTextEditor(Widget):
    """WYSIWYG rich-text editing surface.

    Renders the document with formatting applied. Handles all keyboard
    input: typing, navigation, backspace/delete, selection, undo/redo.
    """

    can_focus = True
    DEFAULT_CSS = """
    RichTextEditor {
        padding: 1 1;
        overflow-y: auto;
        border: solid $secondary;
        background: $background;
        color: $text;
    }
    RichTextEditor:focus {
        border: solid $accent;
    }
    """

    class Changed(Message):
        """Posted when document content changes."""
        def __init__(self, document: Document) -> None:
            self.document = document
            super().__init__()

    def __init__(self, document: Document | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.document = document or Document.empty()
        self._cursor = Cursor()
        self._sel_start: Cursor | None = None
        self._sel_end: Cursor | None = None
        self._pending_bold: bool | None = None
        self._pending_italic: bool | None = None
        self._pending_underline: bool | None = None
        self._undo_stack: list[Document] = []
        self._redo_stack: list[Document] = []

    # ── properties ────────────────────────────────────────────────────────

    @property
    def has_selection(self) -> bool:
        return (self._sel_start is not None and self._sel_end is not None
                and _cmp(self._sel_start, self._sel_end) != 0)

    @property
    def pending_formats(self) -> list[str]:
        """List of pending formatting names (e.g. ['B', 'I'])."""
        res = []
        if self._pending_bold:
            res.append("B")
        if self._pending_italic:
            res.append("I")
        if self._pending_underline:
            res.append("U")
        return res

    # ── cursor helpers ────────────────────────────────────────────────────

    def _cursor_abs(self, c: Cursor | None = None) -> int:
        """Absolute character offset for a cursor position."""
        if c is None:
            c = self._cursor
        pos = 0
        for pi in range(c.para):
            if pi < len(self.document.paragraphs):
                pos += self.document.paragraphs[pi].text_length + 1
        if c.para < len(self.document.paragraphs):
            para = self.document.paragraphs[c.para]
            for si in range(min(c.span, len(para.spans))):
                pos += len(para.spans[si].text)
        pos += c.offset
        return pos

    def _clamp(self, c: Cursor) -> None:
        """Clamp cursor to valid document bounds."""
        if not self.document.paragraphs:
            c.para = c.span = c.offset = 0
            return
        c.para = max(0, min(c.para, len(self.document.paragraphs) - 1))
        para = self.document.paragraphs[c.para]
        if not para.spans:
            c.span = c.offset = 0
            return
        c.span = max(0, min(c.span, len(para.spans) - 1))
        span = para.spans[c.span]
        c.offset = max(0, min(c.offset, len(span.text)))

    def _clear_sel(self) -> None:
        self._sel_start = self._sel_end = None

    def _sel_range(self) -> tuple[Cursor, Cursor]:
        """Return (start, end) cursor for current selection."""
        if not self.has_selection:
            return self._cursor.copy(), self._cursor.copy()
        s, e = self._sel_start, self._sel_end  # type: ignore[assignment]
        if _cmp(s, e) > 0:
            s, e = e, s
        return s.copy(), e.copy()

    def _push_undo(self) -> None:
        self._undo_stack.append(self.document.copy())
        self._redo_stack.clear()
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _post(self) -> None:
        self.refresh(layout=True)
        self.post_message(self.Changed(self.document))

    # ── rendering ─────────────────────────────────────────────────────────

    def render(self) -> Text:
        width = max(self.size.width - 2, 20)  # account for padding
        out = Text()

        for pi, para in enumerate(self.document.paragraphs):
            if pi > 0:
                out.append("\n")

            line = Text()
            for span in para.spans:
                kw: dict = {}
                if span.bold:
                    kw["bold"] = True
                if span.italic:
                    kw["italic"] = True
                if span.underline:
                    kw["underline"] = True
                if span.color:
                    kw["color"] = span.color
                sty = Style(**kw) if kw else Style.null()
                line.append(span.text, sty)

            ll = line.cell_len
            if para.alignment == "center" and ll < width:
                pad = (width - ll) // 2
                if pad > 0:
                    line = Text(" " * pad) + line
            elif para.alignment == "right" and ll < width:
                pad = width - ll
                if pad > 0:
                    line = Text(" " * pad) + line

            out.append_text(line)

        # selection highlight
        if self.has_selection:
            s, e = self._sel_range()
            a = self._cursor_abs(s)
            b = self._cursor_abs(e)
            if a > b:
                a, b = b, a
            plain_len = len(out.plain)
            a = min(a, plain_len)
            b = min(b, plain_len)
            if b > a:
                out.stylize(Style(bgcolor="#44475a"), a, b)

        # cursor
        if self.has_focus and not self.has_selection:
            ca = self._cursor_abs()
            pl = len(out.plain)
            if ca < pl:
                out.stylize(Style(reverse=True), ca, ca + 1)
            else:
                out.append(" ", Style(reverse=True))

        return out

    # ── keyboard input ────────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        key = event.key
        char = event.character
        handled = True

        # printable character (Textual 8.x uses is_printable property)
        if event.is_printable and char:
            self._push_undo()
            self._insert_char(char)
            self._post()
        elif key == "backspace":
            self._push_undo()
            self._backspace()
            self._post()
        elif key == "delete":
            self._push_undo()
            self._delete()
            self._post()
        elif key == "enter":
            self._push_undo()
            self._insert_para()
            self._post()
        elif key in ("left", "right", "up", "down",
                     "shift+left", "shift+right", "shift+up", "shift+down"):
            self._navigate(key)
        elif key == "home":
            self._clear_sel()
            self._clamp(self._cursor)
            self._cursor.span = 0
            self._cursor.offset = 0
        elif key == "end":
            self._clear_sel()
            self._clamp(self._cursor)
            para = self.document.paragraphs[self._cursor.para]
            if para.spans:
                self._cursor.span = len(para.spans) - 1
                self._cursor.offset = len(para.spans[-1].text)
        elif key == "tab":
            self._push_undo()
            self._insert_char("    ")
            self._post()
        else:
            handled = False

        if handled:
            event.prevent_default()
            event.stop()

    def _navigate(self, key: str) -> None:
        # Detect shift modifier from key name (Textual 8.x uses "shift+left" format)
        shift = key.startswith("shift+")
        direction = key.replace("shift+", "") if shift else key

        if shift:
            if not self.has_selection:
                self._sel_start = self._cursor.copy()
                self._sel_end = self._cursor.copy()
        else:
            if self.has_selection:
                s, e = self._sel_range()
                if direction == "left":
                    self._cursor = s.copy()
                elif direction == "right":
                    self._cursor = e.copy()
                elif direction in ("up", "down"):
                    pass  # fall through to normal move
                self._clear_sel()
                if direction in ("left", "right"):
                    self._clamp(self._cursor)
                    return
            self._clear_sel()

        self._clamp(self._cursor)
        if direction == "left":
            self._move_left()
        elif direction == "right":
            self._move_right()
        elif direction == "up":
            self._move_up()
        elif direction == "down":
            self._move_down()

        if shift:
            self._sel_end = self._cursor.copy()

    def _move_left(self) -> None:
        if self._cursor.offset > 0:
            self._cursor.offset -= 1
        elif self._cursor.span > 0:
            self._cursor.span -= 1
            para = self.document.paragraphs[self._cursor.para]
            self._cursor.offset = len(para.spans[self._cursor.span].text)
        elif self._cursor.para > 0:
            self._cursor.para -= 1
            para = self.document.paragraphs[self._cursor.para]
            if para.spans:
                self._cursor.span = len(para.spans) - 1
                self._cursor.offset = len(para.spans[-1].text)
            else:
                self._cursor.span = self._cursor.offset = 0

    def _move_right(self) -> None:
        para = self.document.paragraphs[self._cursor.para]
        if not para.spans:
            if self._cursor.para < len(self.document.paragraphs) - 1:
                self._cursor.para += 1
                self._cursor.span = self._cursor.offset = 0
            return
        span = para.spans[self._cursor.span]
        if self._cursor.offset < len(span.text):
            self._cursor.offset += 1
        elif self._cursor.span < len(para.spans) - 1:
            self._cursor.span += 1
            self._cursor.offset = 0
        elif self._cursor.para < len(self.document.paragraphs) - 1:
            self._cursor.para += 1
            self._cursor.span = self._cursor.offset = 0

    def _move_up(self) -> None:
        if self._cursor.para == 0:
            self._cursor.span = self._cursor.offset = 0
            return
        h = self._cursor_abs(Cursor(self._cursor.para, max(0, self._cursor.span),
                                    self._cursor.offset))
        h0 = self._cursor_abs(Cursor(self._cursor.para, 0, 0))
        h_pos = h - h0
        self._cursor.para -= 1
        self._cursor.span = self._cursor.offset = 0
        para = self.document.paragraphs[self._cursor.para]
        acc = 0
        for si, sp in enumerate(para.spans):
            if acc + len(sp.text) >= h_pos:
                self._cursor.span = si
                self._cursor.offset = h_pos - acc
                break
            acc += len(sp.text)
        else:
            if para.spans:
                self._cursor.span = len(para.spans) - 1
                self._cursor.offset = len(para.spans[-1].text)

    def _move_down(self) -> None:
        if self._cursor.para >= len(self.document.paragraphs) - 1:
            para = self.document.paragraphs[self._cursor.para]
            if para.spans:
                self._cursor.span = len(para.spans) - 1
                self._cursor.offset = len(para.spans[-1].text)
            return
        h = self._cursor_abs(Cursor(self._cursor.para, max(0, self._cursor.span),
                                    self._cursor.offset))
        h0 = self._cursor_abs(Cursor(self._cursor.para, 0, 0))
        h_pos = h - h0
        self._cursor.para += 1
        self._cursor.span = self._cursor.offset = 0
        para = self.document.paragraphs[self._cursor.para]
        acc = 0
        for si, sp in enumerate(para.spans):
            if acc + len(sp.text) >= h_pos:
                self._cursor.span = si
                self._cursor.offset = h_pos - acc
                break
            acc += len(sp.text)
        else:
            if para.spans:
                self._cursor.span = len(para.spans) - 1
                self._cursor.offset = len(para.spans[-1].text)

    # ── text manipulation ─────────────────────────────────────────────────

    def _delete_selection(self) -> None:
        """Remove selected text. Returns True if something was deleted."""
        if not self.has_selection:
            return
        s, e = self._sel_range()
        # Work backwards from end
        self._cursor = e.copy()
        target = self._cursor_abs(s)
        while self._cursor_abs() > target:
            self._backspace_raw()
            self._clamp(self._cursor)
        self._clear_sel()

    def _insert_char(self, ch: str) -> None:
        if self.has_selection:
            self._delete_selection()
        self._clamp(self._cursor)
        para = self.document.paragraphs[self._cursor.para]

        # ensure span exists
        if not para.spans:
            sp = Span()
            para.spans.append(sp)
            self._cursor.span = 0
            self._cursor.offset = 0

        span = para.spans[self._cursor.span]

        # if pending formatting differs from current span, handle boundaries
        pending = self._pending_bold, self._pending_italic, self._pending_underline
        if any(p is not None for p in pending):
            new_b = span.bold if self._pending_bold is None else self._pending_bold
            new_i = span.italic if self._pending_italic is None else self._pending_italic
            new_u = span.underline if self._pending_underline is None else self._pending_underline
            if new_b != span.bold or new_i != span.italic or new_u != span.underline:
                if self._cursor.offset < len(span.text):
                    # split span at cursor
                    right_text = span.text[self._cursor.offset:]
                    span.text = span.text[:self._cursor.offset]
                    new_span = span.copy()
                    new_span.text = ""
                    new_span.bold = new_b
                    new_span.italic = new_i
                    new_span.underline = new_u
                    para.spans.insert(self._cursor.span + 1, new_span)
                    if right_text:
                        right = span.copy()
                        right.text = right_text
                        para.spans.insert(self._cursor.span + 2, right)
                    self._cursor.span += 1
                    self._cursor.offset = 0
                    span = new_span
                elif self._cursor.offset == 0:
                    # at start — apply to this span
                    span.bold = new_b
                    span.italic = new_i
                    span.underline = new_u
                else:
                    # at end — create new span after current
                    new_span = span.copy()
                    new_span.text = ""
                    new_span.bold = new_b
                    new_span.italic = new_i
                    new_span.underline = new_u
                    para.spans.insert(self._cursor.span + 1, new_span)
                    self._cursor.span += 1
                    self._cursor.offset = 0
                    span = new_span

        span.text = span.text[:self._cursor.offset] + ch + span.text[self._cursor.offset:]
        self._cursor.offset += 1
        self._pending_bold = self._pending_italic = self._pending_underline = None

    def _backspace(self) -> None:
        if self.has_selection:
            self._delete_selection()
            return
        self._clamp(self._cursor)
        if self._cursor.offset == 0 and self._cursor.span == 0 and self._cursor.para == 0:
            return
        self._backspace_raw()
        self._clamp(self._cursor)

    def _backspace_raw(self) -> None:
        """Backspace without clamping or selection check."""
        if self._cursor.offset > 0:
            span = self.document.paragraphs[self._cursor.para].spans[self._cursor.span]
            span.text = span.text[:self._cursor.offset - 1] + span.text[self._cursor.offset:]
            self._cursor.offset -= 1
            self._merge_at(self._cursor.para, self._cursor.span)
        elif self._cursor.span > 0:
            para = self.document.paragraphs[self._cursor.para]
            prev = para.spans[self._cursor.span - 1]
            cur = para.spans[self._cursor.span]
            self._cursor.offset = len(prev.text)
            prev.text += cur.text
            para.spans.pop(self._cursor.span)
            self._cursor.span -= 1
            self._merge_at(self._cursor.para, self._cursor.span)
        elif self._cursor.para > 0:
            cur_p = self.document.paragraphs[self._cursor.para]
            prev_p = self.document.paragraphs[self._cursor.para - 1]
            for s in cur_p.spans:
                prev_p.spans.append(s)
            self.document.paragraphs.pop(self._cursor.para)
            self._cursor.para -= 1
            if prev_p.spans:
                self._cursor.span = len(prev_p.spans) - 1
                self._cursor.offset = len(prev_p.spans[-1].text)
            else:
                self._cursor.span = self._cursor.offset = 0

    def _delete(self) -> None:
        if self.has_selection:
            self._delete_selection()
            return
        self._clamp(self._cursor)
        para = self.document.paragraphs[self._cursor.para]
        if not para.spans:
            if self._cursor.para < len(self.document.paragraphs) - 1:
                self.document.paragraphs.pop(self._cursor.para)
            return
        span = para.spans[self._cursor.span]
        if self._cursor.offset < len(span.text):
            span.text = span.text[:self._cursor.offset] + span.text[self._cursor.offset + 1:]
        elif self._cursor.span < len(para.spans) - 1:
            nxt = para.spans[self._cursor.span + 1]
            span.text += nxt.text
            para.spans.pop(self._cursor.span + 1)
            self._merge_at(self._cursor.para, self._cursor.span)
        elif self._cursor.para < len(self.document.paragraphs) - 1:
            nxt_p = self.document.paragraphs[self._cursor.para + 1]
            for s in nxt_p.spans:
                para.spans.append(s)
            self.document.paragraphs.pop(self._cursor.para + 1)

    def _insert_para(self) -> None:
        if self.has_selection:
            self._delete_selection()
        self._clamp(self._cursor)
        para = self.document.paragraphs[self._cursor.para]

        new_spans: list[Span] = []
        if para.spans and self._cursor.span < len(para.spans):
            span = para.spans[self._cursor.span]
            if self._cursor.offset < len(span.text):
                right_text = span.text[self._cursor.offset:]
                span.text = span.text[:self._cursor.offset]
                new_spans.append(Span(text=right_text, bold=span.bold,
                                      italic=span.italic,
                                      underline=span.underline,
                                      color=span.color,
                                      font_size=span.font_size,
                                      font_idx=span.font_idx))
            for si in range(self._cursor.span + 1, len(para.spans)):
                new_spans.append(para.spans[si])
            del para.spans[self._cursor.span + 1:]

        new_p = Paragraph(spans=new_spans, alignment=para.alignment)
        self.document.paragraphs.insert(self._cursor.para + 1, new_p)
        self._cursor.para += 1
        self._cursor.span = self._cursor.offset = 0

    def _merge_at(self, pi: int, si: int) -> None:
        """Merge span at (pi, si) with neighbors if formatting matches."""
        para = self.document.paragraphs[pi]
        # merge with next
        if si + 1 < len(para.spans):
            if para.spans[si].matches_formatting(para.spans[si + 1]):
                para.spans[si].text += para.spans[si + 1].text
                para.spans.pop(si + 1)
        # merge with previous
        if si > 0:
            if para.spans[si - 1].matches_formatting(para.spans[si]):
                if self._cursor.span == si:
                    self._cursor.offset += len(para.spans[si - 1].text)
                    self._cursor.span -= 1
                para.spans[si - 1].text += para.spans[si].text
                para.spans.pop(si)

    # ── public formatting API ─────────────────────────────────────────────

    def toggle_bold(self) -> None:
        self._toggle("bold", "_pending_bold")

    def toggle_italic(self) -> None:
        self._toggle("italic", "_pending_italic")

    def toggle_underline(self) -> None:
        self._toggle("underline", "_pending_underline")

    def _toggle(self, attr: str, pending_attr: str) -> None:
        if self.has_selection:
            self._push_undo()
            self._apply_fmt_to_selection(attr)
            self._post()
        else:
            cur = getattr(self, pending_attr)
            if cur is None:
                self._clamp(self._cursor)
                para = self.document.paragraphs[self._cursor.para]
                if para.spans and self._cursor.span < len(para.spans):
                    cur = getattr(para.spans[self._cursor.span], attr)
                else:
                    cur = False
            setattr(self, pending_attr, not cur)

    def _apply_fmt_to_selection(self, attr: str) -> None:
        """Select text, remove it, re-insert with formatting toggled."""
        if not self.has_selection:
            return
        s, e = self._sel_range()

        # Collect selected spans
        parts: list[Span] = []
        for pi in range(s.para, e.para + 1):
            if pi >= len(self.document.paragraphs):
                break
            para = self.document.paragraphs[pi]
            si0 = s.span if pi == s.para else 0
            si1 = e.span if pi == e.para else len(para.spans) - 1
            for si in range(si0, min(si1 + 1, len(para.spans))):
                sp = para.spans[si]
                txt = sp.text
                if pi == s.para and si == s.span:
                    txt = txt[s.offset:]
                if pi == e.para and si == e.span:
                    txt = txt[:e.offset]
                if txt:
                    sp2 = sp.copy()
                    sp2.text = txt
                    setattr(sp2, attr, not getattr(sp2, attr))
                    parts.append(sp2)

        # Delete selection
        self._delete_selection()

        # Insert at cursor
        for sp in parts:
            para = self.document.paragraphs[self._cursor.para]
            # create span with text
            ns = sp.copy()
            # split text by chars and insert
            for ch in ns.text:
                self._insert_char(ch)

        self._clear_sel()

    def set_alignment(self, alignment: str) -> None:
        self._push_undo()
        self._clamp(self._cursor)
        if self._cursor.para < len(self.document.paragraphs):
            self.document.paragraphs[self._cursor.para].alignment = alignment
        self._post()

    def select_all(self) -> None:
        if not self.document.paragraphs:
            return
        self._sel_start = Cursor(0, 0, 0)
        lp = self.document.paragraphs[-1]
        if lp.spans:
            self._sel_end = Cursor(len(self.document.paragraphs) - 1,
                                   len(lp.spans) - 1, len(lp.spans[-1].text))
        else:
            self._sel_end = Cursor(len(self.document.paragraphs) - 1, 0, 0)
        self._cursor = self._sel_end.copy()

    # ── undo/redo ─────────────────────────────────────────────────────────

    def undo(self) -> None:
        if self._undo_stack:
            self._redo_stack.append(self.document.copy())
            self.document = self._undo_stack.pop()
            self._clamp(self._cursor)
            self._clear_sel()
            self._pending_bold = self._pending_italic = self._pending_underline = None
            self._post()

    def redo(self) -> None:
        if self._redo_stack:
            self._undo_stack.append(self.document.copy())
            self.document = self._redo_stack.pop()
            self._clamp(self._cursor)
            self._clear_sel()
            self._pending_bold = self._pending_italic = self._pending_underline = None
            self._post()

    def load_document(self, doc: Document) -> None:
        self.document = doc
        self._cursor = Cursor()
        self._clear_sel()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._pending_bold = self._pending_italic = self._pending_underline = None
        self._post()


# ═══════════════════════════════════════════════════════════════════════════════
# menu bar
# ═══════════════════════════════════════════════════════════════════════════════

class MenuBar(Horizontal):
    """Horizontal menu bar with dropdown ListView popups."""

    def __init__(self, on_action) -> None:
        super().__init__(id="menu-bar")
        self._on_action = on_action
        self._open_idx: int = -1
        self._dropdown: ListView | None = None

    def compose(self) -> ComposeResult:
        for i, (label, _) in enumerate(MENU_DEF):
            yield Button(label, id=f"menu-btn-{i}", classes="menu-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        idx = int(str(event.button.id).split("-")[-1])
        self._toggle(idx)

    def _toggle(self, idx: int) -> None:
        if self._open_idx == idx:
            self._close()
            return
        self._close()
        self._open_idx = idx
        _, items = MENU_DEF[idx]
        lv = ListView(id="menu-dropdown")
        self._dropdown = lv
        self.mount(lv)
        for label, action in items:
            if not action:
                lv.append(ListItem(Label("─" * 16), disabled=True))
            else:
                lv.append(ListItem(Label(f" {label} ")))
        lv.styles.width = 24
        lv.styles.offset_x = self._btn_offset(idx)
        lv.styles.offset_y = 1
        lv.focus()

    def _close(self) -> None:
        self._open_idx = -1
        if self._dropdown is not None:
            self._dropdown.remove()
            self._dropdown = None

    def _btn_offset(self, idx: int) -> int:
        x = 0
        for i in range(idx):
            x += len(MENU_DEF[i][0]) + 1
        return x

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._open_idx < 0:
            return
        _, items = MENU_DEF[self._open_idx]
        si = event.list_view.index
        if si is not None and si < len(items) and items[si][1]:
            self._close()
            self._on_action(items[si][1])

    def close(self) -> None:
        self._close()

    @property
    def is_open(self) -> bool:
        return self._dropdown is not None


# ═══════════════════════════════════════════════════════════════════════════════
# file‑browser modal
# ═══════════════════════════════════════════════════════════════════════════════

class FileManagerModal(ModalScreen[str | None]):
    """TUI file browser with .rtf filter."""

    BINDINGS = [
        Binding("backspace", "go_up", "Parent"),
        Binding("asciitilde", "go_home", "Home"),
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    CSS = """
    FileManagerModal { align: center middle; }
    #fm-container {
        width: 64; height: 26; background: $surface;
        border: thick $secondary; padding: 1 2;
    }
    #fm-title {
        width: 100%; height: 1; text-style: bold; color: $text;
        content-align: center middle; background: $primary-background; padding: 0 1;
    }
    #fm-path {
        width: 100%; height: 1; color: $text-disabled; padding: 0 1; margin-bottom: 1;
    }
    #fm-list { width: 100%; height: 1fr; }
    #fm-hint {
        width: 100%; height: 1; margin-top: 1;
        color: $text-disabled; content-align: center middle;
    }
    """

    def __init__(self, start_path: str = ".", select_dir: bool = False) -> None:
        super().__init__()
        self._current = Path(start_path).resolve()
        self._entry_map: dict[str, str | Path] = {}
        self._select_dir = select_dir

    def compose(self) -> ComposeResult:
        title = " Choose Save Location " if self._select_dir else " Open File "
        hint = ("[~] Home  [↑↓←→] Navigate  [Enter] Select  [Bksp] Parent  [Esc] Cancel"
                if self._select_dir else
                "[~] Home  [↑↓←→] Navigate  [Enter] Open  [Bksp] Parent  [Esc] Cancel")
        with Vertical(id="fm-container"):
            yield Label(title, id="fm-title")
            yield Label(self._path_display(), id="fm-path")
            yield ListView(id="fm-list")
            yield Label(hint, id="fm-hint")

    def _path_display(self) -> str:
        home = str(Path.home())
        p = str(self._current)
        if p.startswith(home):
            p = "~" + p[len(home):]
        return f" {p}"

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        lv = self.query_one("#fm-list", ListView)
        await lv.clear()
        self._entry_map.clear()
        self.query_one("#fm-path", Label).update(self._path_display())
        if self._current.parent != self._current:
            self._entry_map["fm-parent"] = "parent"
            await lv.append(ListItem(Label(" 📁  .."), id="fm-parent"))
        if self._select_dir:
            self._entry_map["fm-select"] = "select-dir"
            await lv.append(ListItem(Label(" 💾  Save to this directory"), id="fm-select"))
        entries: list[Path] = []
        try:
            entries = sorted(self._current.iterdir(),
                             key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            self.notify("Permission denied", severity="error")
            self._current = self._current.parent
            await self._refresh()
            return
        for idx, p in enumerate(entries):
            if p.name.startswith("."):
                continue
            eid = f"fm-{idx}"
            self._entry_map[eid] = p
            if p.is_dir():
                await lv.append(ListItem(Label(f" 📁  {p.name}"), id=eid))
            else:
                icon = "📝" if p.suffix.lower() == ".rtf" else "📄"
                await lv.append(
                    ListItem(Label(f" {icon}  {p.name}"), id=eid, disabled=self._select_dir))
        lv.index = 0
        lv.focus()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = event.list_view
        if lv.index is None:
            return
        item_id = str(lv.children[lv.index].id) if lv.children[lv.index].id else ""
        target = self._entry_map.get(item_id)
        if target is None:
            return
        if target == "parent":
            self._current = self._current.parent
            await self._refresh()
        elif target == "select-dir":
            self.dismiss(str(self._current))
        elif isinstance(target, Path):
            if target.is_dir():
                self._current = target
                await self._refresh()
            elif not self._select_dir:
                self.dismiss(str(target))

    async def action_go_up(self) -> None:
        if self._current.parent != self._current:
            self._current = self._current.parent
            await self._refresh()

    async def action_go_home(self) -> None:
        self._current = Path.home()
        await self._refresh()

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════════════
# filename‑entry modal
# ═══════════════════════════════════════════════════════════════════════════════

class FileNameModal(ModalScreen[str | None]):
    """Enter a filename. Enter to confirm, Esc to cancel."""

    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]
    CSS = """
    FileNameModal { align: center middle; }
    #fn-container {
        width: 50; background: $surface; border: thick $secondary; padding: 1 2;
    }
    #fn-title {
        width: 100%; height: 1; text-style: bold; color: $text;
        content-align: center middle; margin-bottom: 1;
    }
    #fn-dir { width: 100%; height: 1; color: $text-disabled; padding: 0 1; }
    #fn-input { width: 100%; margin-top: 1; }
    """

    def __init__(self, directory: str) -> None:
        super().__init__()
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="fn-container"):
            yield Label(" Save File ", id="fn-title")
            yield Label(f" {self._directory}", id="fn-dir")
            yield Input(placeholder="filename.rtf", id="fn-input")

    def on_mount(self) -> None:
        self.query_one("#fn-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════════════
# main application
# ═══════════════════════════════════════════════════════════════════════════════

class RTFEditorApp(App):
    """WYSIWYG RTF document editor."""

    CSS = """
    #menu-bar {
        height: 1; background: $surface; border-bottom: solid #bd93f9;
    }
    .menu-btn { min-width: 9; background: $surface; color: $text; border: none; padding: 0 1; }
    .menu-btn:hover { background: #bd93f9; color: #282a36; }
    #menu-dropdown {
        background: #3a1a5e; border: solid #bd93f9;
        height: auto; max-height: 18;
    }
    #menu-dropdown ListItem { padding: 0 1; background: #3a1a5e; color: $text; height: 1; }
    #menu-dropdown ListItem:hover { background: #bd93f9; color: #282a36; }
    #menu-dropdown ListItem:disabled { color: #6272a4; }
    #main { height: 1fr; }
    #rtf-code {
        width: 1fr; border: solid #6272a4; background: $background;
        color: $text;
    }
    #status {
        height: 1; background: $surface; color: $text; padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+o", "open_file", "Open…"),
        Binding("ctrl+b", "bold", "Bold"),
        Binding("ctrl+i", "italic", "Italic"),
        Binding("ctrl+u", "underline", "Underline"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("ctrl+a", "select_all", "Select All"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "escape", "Close menu"),
    ]

    def __init__(self, filepath: str | None = None) -> None:
        super().__init__()
        self._filepath = filepath
        self._save_target_dir: Path | None = None
        self._show_code = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield MenuBar(on_action=self._handle_action)
        with Horizontal(id="main"):
            yield RichTextEditor(id="editor")
            yield TextArea("", id="rtf-code", read_only=True, show_line_numbers=False)
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "dracula"
        editor = self.query_one("#editor", RichTextEditor)
        if self._filepath:
            self._load_file(self._filepath)
        else:
            editor.document = Document.empty()
            editor.load_document(editor.document)
        self._update_rtf_code()
        self._update_status()
        editor.focus()

    # ── file I/O ────────────────────────────────────────────────────────

    def _load_file(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            self.notify(f"Not found: {path}", severity="error")
            return
        try:
            content = p.read_text()
        except Exception as exc:
            self.notify(f"Read error: {exc}", severity="error")
            return
        self._filepath = path
        try:
            doc = RTFParser().parse(content)
        except Exception:
            doc = Document.empty()
        editor = self.query_one("#editor", RichTextEditor)
        editor.load_document(doc)
        self._update_rtf_code()
        self._update_status()

    def _save(self) -> None:
        if not self._filepath:
            self.push_screen(
                FileManagerModal(str(Path.home()), select_dir=True),
                callback=self._on_save_dir,
            )
            return
        editor = self.query_one("#editor", RichTextEditor)
        rtf = generate_rtf(editor.document)
        try:
            Path(self._filepath).write_text(rtf)
        except Exception as exc:
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self._update_status()
        self.notify(f"Saved: {self._filepath}")

    def _on_save_dir(self, directory: str | None) -> None:
        if directory is None:
            return
        self._save_target_dir = Path(directory)
        self.push_screen(FileNameModal(directory), callback=self._on_save_as)

    def _on_save_as(self, filename: str | None) -> None:
        if filename is None or self._save_target_dir is None:
            return
        editor = self.query_one("#editor", RichTextEditor)
        rtf = generate_rtf(editor.document)
        filepath = str(self._save_target_dir / filename)
        try:
            Path(filepath).write_text(rtf)
        except Exception as exc:
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self._filepath = filepath
        self._save_target_dir = None
        self._update_status()
        self.notify(f"Saved: {filepath}")

    # ── RTF code panel ──────────────────────────────────────────────────

    def _update_rtf_code(self) -> None:
        editor = self.query_one("#editor", RichTextEditor)
        try:
            rtf = generate_rtf(editor.document)
        except Exception:
            rtf = "(generation error)"
        self.query_one("#rtf-code", TextArea).text = rtf

    def _toggle_code(self) -> None:
        self._show_code = not self._show_code
        code_panel = self.query_one("#rtf-code", TextArea)
        code_panel.styles.display = "block" if self._show_code else "none"

    # ── status bar ──────────────────────────────────────────────────────

    def _update_status(self) -> None:
        editor = self.query_one("#editor", RichTextEditor)
        fp = self._filepath or "[new file]"
        chars = sum(p.text_length for p in editor.document.paragraphs)
        lines = len(editor.document.paragraphs)
        pending = editor.pending_formats
        pstr = f" | pending: {'+'.join(pending)}" if pending else ""
        self.query_one("#status", Label).update(
            f" {fp}  |  {lines} lines  |  {chars} chars{pstr}")

    # ── actions ─────────────────────────────────────────────────────────

    def _handle_action(self, action: str) -> None:
        editor = self.query_one("#editor", RichTextEditor)
        if action == "new":
            self._filepath = None
            editor.load_document(Document.empty())
            self._update_rtf_code()
            self._update_status()
        elif action == "open":
            start = str(Path(self._filepath).parent) if self._filepath else str(Path.home())
            self.push_screen(FileManagerModal(start), callback=self._on_open)
        elif action == "save":
            self._save()
        elif action == "save_as":
            start = str(Path(self._filepath).parent) if self._filepath else str(Path.home())
            self.push_screen(
                FileManagerModal(start, select_dir=True), callback=self._on_save_dir)
        elif action == "quit":
            self.exit()
        elif action == "undo":
            editor.undo()
            self._update_rtf_code()
        elif action == "redo":
            editor.redo()
            self._update_rtf_code()
        elif action == "select_all":
            editor.select_all()
        elif action == "insert_bold":
            editor.toggle_bold()
            self._update_status()
        elif action == "insert_italic":
            editor.toggle_italic()
            self._update_status()
        elif action == "insert_underline":
            editor.toggle_underline()
            self._update_status()
        elif action == "align_left":
            editor.set_alignment("left")
        elif action == "align_center":
            editor.set_alignment("center")
        elif action == "align_right":
            editor.set_alignment("right")
        elif action == "toggle_preview":
            self._toggle_code()
        elif action.startswith("theme:"):
            self.theme = action.split(":", 1)[1]
            self.notify(f"Theme: {action.split(':', 1)[1]}")

    def _on_open(self, path: str | None) -> None:
        if path is not None:
            self._load_file(path)

    # ── events ──────────────────────────────────────────────────────────

    def on_rich_text_editor_changed(self, event: RichTextEditor.Changed) -> None:
        self._update_rtf_code()
        self._update_status()

    def action_escape(self) -> None:
        menu = self.query_one(MenuBar)
        if menu.is_open:
            menu.close()
            self.query_one("#editor", RichTextEditor).focus()

    def action_save(self) -> None:
        self._save()

    def action_open_file(self) -> None:
        self._handle_action("open")

    def action_bold(self) -> None:
        self._handle_action("insert_bold")

    def action_italic(self) -> None:
        self._handle_action("insert_italic")

    def action_underline(self) -> None:
        self._handle_action("insert_underline")

    def action_undo(self) -> None:
        self._handle_action("undo")

    def action_redo(self) -> None:
        self._handle_action("redo")

    def action_select_all(self) -> None:
        self._handle_action("select_all")

    def action_quit(self) -> None:
        self.exit()


if __name__ == "__main__":
    fp = sys.argv[1] if len(sys.argv) > 1 else None
    RTFEditorApp(filepath=fp).run()
