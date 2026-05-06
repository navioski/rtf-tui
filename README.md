# rtf-tui

A WYSIWYG RTF document editor for the terminal. Edit rich text like Microsoft Word — RTF source code is generated automatically.

![screenshot](https://img.shields.io/badge/platform-terminal-green)

## Features

- **WYSIWYG editing** — type and format text directly, see the result immediately
- **Keyboard-driven** — all formatting via shortcuts, no mouse required
- **Live RTF generation** — the RTF source updates in real time as you edit
- **Open & save .rtf files** — compatible with standard RTF documents
- **Undo/redo** — full editing history
- **Multiple themes** — Dracula, Nord, Gruvbox, Catppuccin, Solarized

## Installation

```bash
pip install textual rich
```

Requires Python 3.10+.

## Usage

```bash
# New document
python3 rtf_tui.py

# Open existing RTF file
python3 rtf_tui.py document.rtf
```

## Keyboard Shortcuts

### Editing

| Key | Action |
|-----|--------|
| `Ctrl+B` | Toggle bold |
| `Ctrl+I` | Toggle italic |
| `Ctrl+U` | Toggle underline |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+A` | Select all |
| `Shift+Arrows` | Extend selection |
| `Enter` | New paragraph |
| `Backspace` | Delete backward |
| `Delete` | Delete forward |
| `Home/End` | Jump to line start/end |

### File Operations

| Key | Action |
|-----|--------|
| `Ctrl+S` | Save |
| `Ctrl+O` | Open file |
| `Ctrl+Q` | Quit |
| `Esc` | Close menu |

## Layout

```
┌─────────────────────────────────────────────────────┐
│ Header / Menu Bar                                   │
├────────────────────────┬────────────────────────────┤
│                        │                            │
│   WYSIWYG Editor       │   Generated RTF Code       │
│   (edit document)      │   (auto-updating)          │
│                        │                            │
├────────────────────────┴────────────────────────────┤
│ Status Bar / Footer                                  │
└─────────────────────────────────────────────────────┘
```

## Supported RTF Features

- **Font styles**: bold, italic, underline
- **Alignment**: left, center, right
- **Colors**: red, blue, green (extensible)
- **Font sizes** (via RTF import)
- **Multiple paragraphs**

## File Structure

```
rtf-tui/
├── rtf_tui.py      # Main application (~1300 lines)
├── test.rtf        # Sample RTF file for testing
└── README.md
```

## License

MIT
