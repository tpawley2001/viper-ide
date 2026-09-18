# Viper IDE

A full-featured Python IDE for Windows that notices when your code imports a package
you don't have, and installs it for you.

![Viper logo](viper_ide/resources/viper.png)

## Smart downloads

Viper watches what your code needs and gets it, instead of leaving you to decode
`ModuleNotFoundError`:

| Situation | What Viper does |
|---|---|
| A file imports something that isn't installed | A bar above the editor names the **PyPI package** (e.g. `import cv2` → `opencv-python`, `from PIL import Image` → `Pillow`, `import yaml` → `PyYAML`). The import line gets a squiggle. One click installs it. |
| You press **Run** with missing imports | Asks **Install and Run / Run Anyway / Cancel**. |
| A program dies with `ModuleNotFoundError` at runtime (dynamic imports) | Offers to install the package and re-run. It won't loop: if the import still fails after installing, it stops asking. |
| The project has `requirements.txt` or `pyproject.toml` dependencies that aren't installed | A bar offers to install them. |
| A script has PEP 723 inline metadata (`# /// script`) | Its `dependencies` are checked too. |
| No Python on the machine | Offers **Download Python**: fetches the official python.org installer, verifies its Authenticode signature is the Python Software Foundation's, and installs per-user (no admin, nothing on PATH). Falls back to python.org's portable NuGet package. |
| An interpreter has no pip | Bootstraps it with `ensurepip`, then retries. |
| You ask to format code | Downloads Black or Ruff into Viper's own tools folder, so your project environments aren't touched. |

Every name is checked against PyPI before it's offered, and imports that can't
be matched to a real package are reported as "not found on PyPI" rather than
installed blindly. Imports inside `try/except ImportError`, `if TYPE_CHECKING:`
or platform checks count as optional and never prompt. Set **Settings → When packages are
missing** to *Ask* (default), *Install automatically* or *Never offer*.

## AI Assistant

Connect Viper to any **OpenAI-compatible** chat server (OpenAI, OpenRouter, llama.cpp /
llama-swap, Ollama, LM Studio, vLLM...) and ask it to change your code without leaving the editor.

1. **Add a provider**: press **Manage...** in the AI Assistant panel (or **Settings → Manage AI Providers**,
   or **Code → AI Provider**), then **Add** a preset (OpenAI, OpenRouter, Google Gemini, Groq, Mistral,
   DeepSeek, Ollama, LM Studio, llama.cpp) or a custom server URL. Give it an API key, or the name of an
   environment variable to read it from (presets fill in the usual one, e.g. `OPENAI_API_KEY`). **Test & Load
   Models** checks the connection and fills the model list.
2. Open the panel with the ✦ toolbar button (`Ctrl+Shift+A`), or select code and press **`Ctrl+I`**
   (*Ask AI to Edit*, also in the editor's right-click menu).
3. Describe the change. The open file (and your selection) go along with the message when
   **Send the current file** is ticked. Replies stream in.
4. The assistant proposes edits as SEARCH/REPLACE blocks. **Review & Apply** shows a diff and applies
   it as **one undo step** (`Ctrl+Z` reverts all of it). Nothing changes until you apply. Blocks that don't
   match are listed, and **Ask to Fix** sends them back to the model.

**Switching providers:** add as many as you like and switch from the drop-down at the top of the panel or
**Code → AI Provider**. Each provider remembers its own model, and its key is sent only to its own server.
Switching mid-conversation carries on the same chat with the new model. Settings from 1.2.x become a
provider named *Default*.

Follow-up messages keep the conversation, and only the latest message includes the file, so
long chats don't resend it every time. Matching tolerates trailing whitespace, stray code fences and
indentation shifts, and CRLF files stay CRLF. Keys are stored in `settings.json` in plain text; use the environment-variable option to keep them out of it.

## Features

- **Editor** (QScintilla): Python highlighting, code folding, indent guides, brace matching,
  auto-closing brackets and quotes, smart indentation, multi-cursor (`Ctrl+Shift+L`, Alt+drag),
  highlighting of other occurrences, find/replace with regex, line moving and duplication,
  comment toggling, CRLF/LF and encoding preservation (including Python encoding declarations),
  atomic saves, and reload when a file changes on disk.
- **Code intelligence** (Jedi, run against *your* interpreter so installed packages are
  understood): completion, signature help, hover docs, go to definition (`F12` / Ctrl+click),
  find references, project-wide rename with diff preview.
- **Live error checking** (pyflakes): squiggles, margin markers, and a Problems panel.
- **Run** (`F5`) with program input, clickable tracebacks, arguments, and stop/rerun.
- **Debugger** (`F6`): breakpoints (click the margin) with conditions, step over/into/out,
  pause, call stack, variable tree with expandable objects, a debug console that evaluates in
  the selected frame, post-mortem on uncaught exceptions, and "Just My Code".
- **Python console** with history. Run the selection (`Shift+Enter`) or a `# %%` cell (`Ctrl+Enter`).
- **Terminal** with the selected interpreter activated, plus "Open External Terminal".
- **Interpreters**: auto-discovers the py launcher, registry, PATH, conda, pyenv, and project
  `.venv`s; per-project selection; one-click **Create Virtual Environment**.
- **Packages panel**: install (with PyPI lookup), uninstall, check for updates, upgrade,
  install or export `requirements.txt`.
- **Project explorer**, outline, find in files, command palette (`Ctrl+Shift+P`),
  go to file (`Ctrl+P`), session restore, and dark/light themes.
- **Windows integration**: per-user installer, Start menu, and optional "Edit with Viper IDE"
  on `.py` files and folders.

## Keyboard shortcuts

| Action | Keys | Action | Keys |
|---|---|---|---|
| Run file | `F5` | Command palette | `Ctrl+Shift+P` |
| Debug file | `F6` | Go to file | `Ctrl+P` |
| Stop | `Shift+F5` | Find / Replace | `Ctrl+F` / `Ctrl+H` |
| Continue | `F8` | Find in files | `Ctrl+Shift+F` |
| Step over / into / out | `F10` / `F11` / `Shift+F11` | Go to line | `Ctrl+G` |
| Toggle breakpoint | `F9` | Toggle comment | `Ctrl+/` |
| Go to definition | `F12` | Duplicate line | `Ctrl+D` |
| Find references | `Shift+F12` | Move line | `Alt+Up` / `Alt+Down` |
| Rename symbol | `F2` | Format document | `Ctrl+Alt+L` |
| Show docs | `Ctrl+Q` | Completion | `Ctrl+Space` |
| Run selection in console | `Shift+Enter` | Run `# %%` cell | `Ctrl+Enter` |
| AI Assistant | `Ctrl+Shift+A` | Ask AI to edit | `Ctrl+I` |

## Install

Run `ViperIDE_Setup_<version>.exe`. It installs for the current user by default (no admin
prompt); choose "Install for all users" in the first dialog to install system-wide. Windows 10/11 x64.

### Updates

Installed copies update themselves. A few seconds after launch Viper reads `version.json` from the
update server and, if a newer version is published, shows an **Update Now** bar (or use
**Help → Check for Updates**). The installer is downloaded, **refused unless its sha256 matches the
manifest**, then run silently. Viper closes (prompting for unsaved files) and reopens on the new
version.

Feeds are tried in order: **Settings → Update server URLs**, the `VIPER_UPDATE_URLS` environment
variable (comma separated), then the feeds built into the installer. To point your own builds at your
server, create `viper_ide/resources/update_feeds.txt` (one base URL per line, `#` comments) before
building. It's gitignored, so a build from a clean checkout has no built-in feed and only checks the
URLs you configure. Turn off the startup check in Settings.

Publish a build (after `scripts/build_on_winbox.sh`):

```bash
scripts/publish_update.sh "What changed"      # copies dist/ViperIDE_Setup_<version>.exe + writes version.json
```

It writes the installer and then, atomically, `version.json` (`versionName`, `versionCode`, `file`,
`sha256`, `size`, `publishedAt`, `notes`) into `VIPER_UPDATE_DIR` (default `/var/www/html/viper/windows`),
and keeps the three newest installers. Bump `__version__` in `viper_ide/__init__.py` before building.

Settings live in `%APPDATA%\ViperIDE\settings.json`. Downloaded Pythons, formatter tools and
the log (`viper.log`) are in `%LOCALAPPDATA%\ViperIDE`.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m viper_ide [file-or-folder ...]
```

Tests (headless):

```bash
QT_QPA_PLATFORM=offscreen ./run-dev.sh -m pytest -q tests
QT_QPA_PLATFORM=offscreen ./run-dev.sh -m viper_ide --selftest result.json   # end-to-end, writes a screenshot
```

`run-dev.sh` just runs the venv's Python, with `.gl-libs/` on the library path for Linux hosts
that lack a system libGL.

### Layout

| Path | What |
|---|---|
| `viper_ide/app.py` | Main window: tabs, docks, actions, interpreter selection, smart-install flow, run/debug |
| `viper_ide/editor.py` | QScintilla editor, find bar, editor page |
| `viper_ide/fileio.py` | Source encoding detection and atomic document saves |
| `viper_ide/imports.py` | Import extraction, import→PyPI mapping, requirements/PEP 723 parsing, PyPI lookups |
| `viper_ide/interpreters.py` | Interpreter discovery/probing, venv creation, python.org download and install |
| `viper_ide/packages.py` | Queued pip runner and on-demand formatter tools |
| `viper_ide/intel.py` | Jedi + pyflakes worker thread, outline |
| `viper_ide/helpers/` | Scripts that run inside the *user's* interpreter: `find_missing.py`, `viper_dbg.py` (debugger backend, JSON over a localhost socket) |
| `viper_ide/assistant.py`, `assistantui.py`, `providersui.py` | AI Assistant: providers and presets, OpenAI-compatible streaming client, SEARCH/REPLACE edit parsing and applying, chat dock, Manage Providers dialog |
| `viper_ide/updater.py`, `updateui.py` | Remote self-update: feed check, sha256-verified download, silent installer hand-off |
| `viper_ide/selftest.py` | `--selftest`, which also runs from the frozen build |
| `packaging/` | PyInstaller spec, Inno Setup script, `build_windows.ps1` |

### Building the Windows installer

On Windows, with Python 3.12+ and [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SelfTest
```

This writes `dist\ViperIDE\` (the app) and `dist\ViperIDE_Setup_<version>.exe`. From Linux,
`scripts/build_on_winbox.sh --selftest` ships the source to a Windows build host over SSH
(`WINBOX` env var, default `winbox`), builds there, and copies the installer back.
`scripts/win_line_endings.py` keeps the `.ps1`/`.iss` files ASCII with CRLF line endings
(plus a BOM on `.ps1`), which Windows PowerShell 5.1 needs.

## License

MIT. Copyright (c) 2026 Hillyard Tech.
