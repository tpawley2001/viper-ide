"""OpenAI-compatible chat client and the edit format the assistant answers in.

Works with anything that speaks ``POST {base}/chat/completions`` (OpenAI, llama.cpp /
llama-swap, Ollama, LM Studio, vLLM, OpenRouter...). Only the standard library is
used so the frozen build needs nothing extra.

Edits come back as SEARCH/REPLACE blocks (the format aider popularised) because
small local models reproduce a few lines far more reliably than a whole file:

    <<<<<<< SEARCH
    exact lines from the file
    =======
    their replacement
    >>>>>>> REPLACE

Code that belongs in a file of its own comes back as a NEW FILE block, which the IDE
opens in a new editor tab:

    <<<<<<< NEW FILE: name.py
    the whole file
    >>>>>>> END
"""
from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

USER_AGENT = f"ViperIDE/{__version__}"
MAX_FILE_CHARS = 120_000

SYSTEM_PROMPT = """You are the coding assistant built into Viper IDE, a Python IDE.
Answer questions concisely. When the user asks you to change code, reply with a short
explanation followed by one or more edit blocks in exactly this format:

<<<<<<< SEARCH
lines copied exactly from the current file, including indentation
=======
the new lines that replace them
>>>>>>> REPLACE

Rules for edit blocks:
- SEARCH must match the current file text exactly and be unique in it; include a few
  surrounding lines if needed to make it unique. Keep it as short as possible.
- To insert code, SEARCH for the neighbouring lines and repeat them in the replacement.
- To delete code, leave the replacement empty.
- To create or entirely rewrite an empty file, use an empty SEARCH section.
- Never use line numbers, "..." or placeholders; do not wrap blocks in markdown fences.
- Edit blocks only apply to the file you were shown.

When the user asks for a new file, a new tab, a separate script or module, or code that
doesn't belong in the current file, don't edit the current file. Write the code as a new
file instead; the IDE opens each one in its own editor tab:

<<<<<<< NEW FILE: short_descriptive_name.py
the complete contents of the new file
>>>>>>> END

Write the whole file (no placeholders or "..."), and don't wrap it in markdown fences.
Repeating a NEW FILE block with the same name replaces that tab's contents."""


class AssistantError(Exception):
    pass


@dataclass
class Edit:
    search: str
    replace: str


@dataclass
class NewFile:
    name: str
    content: str


def normalise_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url):
        url = "http://" + url
    for suffix in ("/chat/completions", "/models"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


# name, base URL, environment variable the key falls back to. All speak the OpenAI chat API.
PRESETS = [
    ("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    ("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    ("Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    ("DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ("Ollama (local)", "http://localhost:11434/v1", ""),
    ("LM Studio (local)", "http://localhost:1234/v1", ""),
    ("llama.cpp server (local)", "http://localhost:8080/v1", ""),
]


def new_provider(name: str = "", base_url: str = "", env_key: str = "") -> dict:
    return {"name": name, "base_url": base_url, "api_key": "", "env_key": env_key, "model": ""}


def providers(settings) -> list[dict]:
    """The saved providers. A 1.2.x single-server setup becomes a provider named "Default"."""
    items = [dict(new_provider(), **p) for p in settings.get("ai_providers") or [] if isinstance(p, dict)]
    legacy = settings.get("ai_base_url")
    if not items and legacy:
        items = [dict(new_provider("Default", legacy), api_key=settings.get("ai_api_key") or "",
                      env_key="OPENAI_API_KEY", model=settings.get("ai_model") or "")]
        save_providers(settings, items, "Default")
    return items


def save_providers(settings, items: list[dict], active: str | None = None) -> None:
    settings._data["ai_providers"] = [dict(p) for p in items]
    if active is not None:
        settings._data["ai_provider"] = active
    for key in ("ai_base_url", "ai_api_key", "ai_model"):
        settings._data.pop(key, None)
    settings.save()


def active_provider(settings) -> dict | None:
    items = providers(settings)
    name = settings.get("ai_provider")
    return next((p for p in items if p["name"] == name), items[0] if items else None)


def set_active(settings, name: str) -> None:
    settings.set("ai_provider", name)


def set_model(settings, name: str, model: str) -> None:
    items = providers(settings)
    for p in items:
        if p["name"] == name:
            p["model"] = model
    save_providers(settings, items)


def api_key(provider: dict | None) -> str:
    """The provider's own key, else its environment variable (never another provider's key)."""
    if not provider:
        return ""
    env = provider.get("env_key") or ""
    return (provider.get("api_key") or (os.environ.get(env) if env else "") or "").strip()


def _request(base: str, path: str, key: str, body: dict | None = None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    return urllib.request.Request(f"{base}{path}", data=data, headers=headers)


def _http_error(e: urllib.error.HTTPError) -> AssistantError:
    try:
        payload = json.loads(e.read().decode("utf-8", "replace"))
        err = payload.get("error", payload)
        detail = err.get("message") if isinstance(err, dict) else str(err)
    except (OSError, ValueError, AttributeError):
        detail = e.reason
    return AssistantError(f"HTTP {e.code}: {detail}")


def list_models(base: str, key: str, timeout: float = 10.0) -> list[str]:
    base = normalise_base(base)
    if not base:
        raise AssistantError("This AI provider has no server URL. Set one in Manage Providers.")
    try:
        with urllib.request.urlopen(_request(base, "/models", key), timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None
    except (OSError, ValueError) as e:
        raise AssistantError(f"Couldn't reach {base}: {e}") from None
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    names = [m.get("id") or m.get("name") if isinstance(m, dict) else str(m) for m in items or []]
    return sorted({n for n in names if n}, key=str.lower)


def stream_chat(base: str, key: str, model: str, messages: list[dict], temperature: float | None = None,
                cancel=None, progress=None, timeout: float = 300.0) -> str:
    """Stream a completion, calling ``progress(text_so_far)``; returns the full reply.

    ``cancel`` is a threading.Event; setting it stops reading and returns what arrived.
    Servers that ignore ``stream`` and answer with one JSON body are handled too.
    """
    base = normalise_base(base)
    if not base:
        raise AssistantError("This AI provider has no server URL. Set one in Manage Providers.")
    body: dict = {"messages": messages, "stream": True}
    if model:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    text, thinking = [], False
    try:
        with urllib.request.urlopen(_request(base, "/chat/completions", key, body), timeout=timeout) as r:
            if "text/event-stream" not in (r.headers.get("Content-Type") or ""):
                data = json.load(r)
                return _message_text(data)
            for raw in r:
                if cancel is not None and cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("error"):
                    err = event["error"]
                    raise AssistantError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        text.append(piece)
                    elif delta.get("reasoning_content") or delta.get("reasoning"):
                        thinking = True
                if progress and (text or thinking):
                    progress("".join(text) if text else None)
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None
    except (OSError, ValueError) as e:
        if text:  # a dropped connection mid-reply still leaves something useful
            return "".join(text) + f"\n\n[connection lost: {e}]"
        raise AssistantError(f"Couldn't reach {base}: {e}") from None
    return "".join(text)


def _message_text(data: dict) -> str:
    try:
        return data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AssistantError(f"Unexpected reply from the server: {json.dumps(data)[:300]}") from None


MAX_ERROR_CHARS = 6000
_TRACEBACK = "Traceback (most recent call last):"
_ERROR_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*)?(?:Error|Exception|Exit|Interrupt)\b.*$|^\s*File \".*\", line \d+",
                         re.MULTILINE)


def error_excerpt(output: str) -> str | None:
    """The traceback (or error report) at the end of a program's output, or None if it didn't error."""
    if not output:
        return None
    i = output.rfind(_TRACEBACK)
    if i >= 0:
        text = output[i:]
    elif _ERROR_LINE.search("\n".join(output.splitlines()[-40:])):
        text = "\n".join(output.splitlines()[-40:])  # e.g. a SyntaxError, which has no "Traceback" header
    else:
        return None
    text = text.strip("\r\n").rstrip()
    if len(text) > MAX_ERROR_CHARS:  # keep the end: that's where the exception is
        text = "...\n" + text[-MAX_ERROR_CHARS:]
    return text


MAX_RED_CHARS = 4000
_PROMPT_ONLY = re.compile(r"^\s*(?:>>>|\.\.\.)\s*$")


def clean_red(text: str, limit: int = MAX_RED_CHARS) -> str:
    """Red (stderr) text for the model: no bare console prompts, trimmed to its most recent part."""
    lines = [ln for ln in (text or "").splitlines() if not _PROMPT_ONLY.match(ln)]
    out = "\n".join(lines).strip("\n").rstrip()
    if len(out) > limit:
        out = "...\n" + out[-limit:]
    return out


def context_message(path: str, text: str, selection: tuple[int, int, str] | None,
                    problems: list[dict] | None = None, red: list[tuple[str, str]] | None = None) -> str:
    """The current file plus what the IDE shows about it: selection, lint problems, and red output.

    ``red`` is [(where, text)]: everything the IDE is showing in red, e.g. a run's stderr.
    """
    name = path or "untitled.py"
    body = text
    note = ""
    if len(body) > MAX_FILE_CHARS:
        body = body[:MAX_FILE_CHARS]
        note = f"\n(The file is truncated to its first {MAX_FILE_CHARS} characters.)"
    parts = [f"Current file: {name}{note}\n<file>\n{body}\n</file>"]
    if selection:
        first, last, sel = selection
        parts.append(f"The user has selected lines {first}-{last}:\n<selection>\n{sel}\n</selection>")
    if problems:
        lines = [f"line {p.get('line', '?')}: {p.get('severity', 'error')}: {p.get('message', '')}"
                 for p in sorted(problems, key=lambda p: p.get("line", 0))[:50]]
        parts.append("Problems the IDE's checker (pyflakes) reports in this file:\n<problems>\n"
                     + "\n".join(lines) + "\n</problems>")
    shown = [(where, clean_red(t)) for where, t in red or []]
    shown = [(where, t) for where, t in shown if t]
    if shown:
        parts.append("Errors and warnings the IDE is currently showing in red:\n" + "\n".join(
            f'<red_output source="{where}">\n{t}\n</red_output>' for where, t in shown))
    return "\n\n".join(parts)


_BLOCK = re.compile(
    r"^[ \t]*<{5,9} ?SEARCH[^\n]*\n(.*?)^[ \t]*={5,9}[ \t]*\n(.*?)^[ \t]*>{5,9} ?REPLACE[^\n]*$",
    re.DOTALL | re.MULTILINE)


_NEW_FILE = re.compile(
    r"^[ \t]*<{5,9} ?NEW[ _]FILE\b:?[ \t]*([^\n]*)\n(.*?)^[ \t]*>{5,9} ?END\b[^\n]*$",
    re.DOTALL | re.MULTILINE)
DEFAULT_NEW_FILE = "new_file.py"


def parse_edits(reply: str) -> list[Edit]:
    reply = _NEW_FILE.sub("", reply)  # a new file's contents are never edits to the current one
    return [Edit(_strip_fence(m.group(1)), _strip_fence(m.group(2))) for m in _BLOCK.finditer(reply)]


def parse_new_files(reply: str) -> list[NewFile]:
    """NEW FILE blocks, in order; a name given twice keeps its last contents."""
    files: dict[str, str] = {}
    for m in _NEW_FILE.finditer(reply):
        name = safe_file_name(m.group(1))
        files.pop(name, None)
        files[name] = _strip_fence(m.group(2))
    return [NewFile(n, c) for n, c in files.items()]


def safe_file_name(name: str) -> str:
    """A bare file name from whatever the model wrote after NEW FILE: (no directories or quoting)."""
    name = name.strip().strip("`'\"*").strip()
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name).strip(" .")
    return name or DEFAULT_NEW_FILE


def _strip_fence(section: str) -> str:
    # Models sometimes put ``` fences inside the sections despite being told not to.
    lines = section.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _line_spans(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for line in text.splitlines(keepends=True):
        spans.append((pos, pos + len(line)))
        pos += len(line)
    return spans


def _find_loose(text: str, search: str) -> tuple[int, int] | None:
    """Match whole lines ignoring trailing whitespace, then ignoring a common indent shift."""
    want = search.split("\n")
    spans = _line_spans(text)
    have = [text[a:b].rstrip("\r\n") for a, b in spans]
    n = len(want)
    for strict in (True, False):
        def key(s):
            return s.rstrip() if strict else s.strip()
        target = [key(w) for w in want]
        hits = [i for i in range(len(have) - n + 1) if [key(h) for h in have[i:i + n]] == target]
        if len(hits) == 1:
            i = hits[0]
            end = spans[i + n - 1][1]
            while end > spans[i + n - 1][0] and text[end - 1] in "\r\n":
                end -= 1
            return spans[i][0], end
        if len(hits) > 1:
            return None
    return None


def _reindent(replace: str, matched: str, search: str) -> str:
    """When SEARCH matched only after an indent shift, shift the replacement the same way."""
    def indent(s):
        first = next((line for line in s.split("\n") if line.strip()), "")
        return first[: len(first) - len(first.lstrip())]
    got, sent = indent(matched), indent(search)
    if got == sent:
        return replace
    out = []
    for line in replace.split("\n"):
        if line.startswith(sent):
            line = got + line[len(sent):]
        elif line.strip():
            line = got + line.lstrip()
        out.append(line)
    return "\n".join(out)


def apply_edits(text: str, edits: list[Edit]) -> tuple[str, list[str]]:
    """Apply edits in order. Returns the new text and a message for each edit that didn't apply."""
    eol = "\r\n" if "\r\n" in text else "\n"
    problems = []
    for i, ed in enumerate(edits, 1):
        search = ed.search.replace("\r\n", "\n")
        replace = ed.replace.replace("\r\n", "\n")
        if eol != "\n":
            search_eol, replace = search.replace("\n", eol), replace.replace("\n", eol)
        else:
            search_eol = search
        if not search.strip():
            if text.strip():
                problems.append(f"edit {i}: has an empty SEARCH but the file isn't empty")
                continue
            text = replace + ("" if replace.endswith(eol) or not replace else eol)
            continue
        count = text.count(search_eol)
        if count == 1:
            text = text.replace(search_eol, replace, 1)
            continue
        if count > 1:
            problems.append(f"edit {i}: SEARCH text appears {count} times; it must be unique")
            continue
        span = _find_loose(text, search)
        if span is None:
            first = search.strip().splitlines()[0] if search.strip() else ""
            problems.append(f"edit {i}: couldn't find the SEARCH text in the file (starts {first[:60]!r})")
            continue
        a, b = span
        text = text[:a] + _reindent(replace, text[a:b], search_eol) + text[b:]
    return text, problems


def unified_diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        f"a/{name}", f"b/{name}"))
