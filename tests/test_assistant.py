"""AI assistant: edit-block parsing/applying, the OpenAI-compatible client, and the dock end to end."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from viper_ide import assistant  # noqa: E402

SOURCE = "def add(a, b):\n    return a + b\n\n\nprint(add(2, 3))\n"

REPLY = """Adding type hints.

<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a: int, b: int) -> int:
    return a + b
>>>>>>> REPLACE
"""


def test_parse_and_apply():
    edits = assistant.parse_edits(REPLY)
    assert len(edits) == 1
    new, problems = assistant.apply_edits(SOURCE, edits)
    assert not problems
    assert new.startswith("def add(a: int, b: int) -> int:\n    return a + b\n\n")


def test_apply_keeps_crlf_and_tolerates_whitespace_and_fences():
    src = SOURCE.replace("\n", "\r\n")
    reply = ("<<<<<<< SEARCH\n```python\nprint(add(2, 3))   \n```\n=======\nprint(add(4, 5))\n>>>>>>> REPLACE")
    new, problems = assistant.apply_edits(src, assistant.parse_edits(reply))
    assert not problems
    assert new == src.replace("print(add(2, 3))", "print(add(4, 5))")


def test_apply_reindents_a_shifted_match():
    src = "class A:\n    def f(self):\n        return 1\n"
    reply = "<<<<<<< SEARCH\ndef f(self):\n    return 1\n=======\ndef f(self):\n    return 2\n>>>>>>> REPLACE"
    new, problems = assistant.apply_edits(src, assistant.parse_edits(reply))
    assert not problems
    assert new == "class A:\n    def f(self):\n        return 2\n"


def test_apply_reports_missing_and_ambiguous():
    src = "x = 1\nx = 1\n"
    edits = [assistant.Edit("x = 1", "x = 2"), assistant.Edit("y = 1", "y = 2")]
    new, problems = assistant.apply_edits(src, edits)
    assert new == src and len(problems) == 2
    assert "2 times" in problems[0] and "couldn't find" in problems[1]


def test_empty_search_fills_empty_file():
    new, problems = assistant.apply_edits("", [assistant.Edit("", "print('hi')")])
    assert new == "print('hi')\n" and not problems


NEW_FILE_REPLY = """Put the helper in its own module.

<<<<<<< NEW FILE: `utils/stats.py`
```python
def mean(xs):
    return sum(xs) / len(xs)
```
>>>>>>> END

<<<<<<< SEARCH
print(add(2, 3))
=======
from stats import mean
print(add(2, 3), mean([1, 2]))
>>>>>>> REPLACE
"""


def test_parse_new_files():
    files = assistant.parse_new_files(NEW_FILE_REPLY)
    assert [(f.name, f.content) for f in files] == [("stats.py", "def mean(xs):\n    return sum(xs) / len(xs)")]
    assert len(assistant.parse_edits(NEW_FILE_REPLY)) == 1
    # A SEARCH/REPLACE block inside a new file's contents is file content, not an edit.
    nested = "<<<<<<< NEW FILE: a.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n>>>>>>> END\n"
    assert assistant.parse_edits(nested) == [] and len(assistant.parse_new_files(nested)) == 1
    assert assistant.safe_file_name(' "C:\\x\\..\\my:file?.py" ') == "myfile.py"
    assert assistant.safe_file_name("``") == assistant.DEFAULT_NEW_FILE
    twice = "<<<<<<< NEW FILE: a.py\n1\n>>>>>>> END\n<<<<<<< NEW FILE: a.py\n2\n>>>>>>> END\n"
    assert [(f.name, f.content) for f in assistant.parse_new_files(twice)] == [("a.py", "2")]


def test_display_markdown_shows_new_files():
    from viper_ide.assistantui import display_markdown

    out = display_markdown(NEW_FILE_REPLY)
    assert "**New tab: stats.py**" in out and "NEW FILE" not in out and "```diff" in out
    partial = display_markdown("Sure.\n\n<<<<<<< NEW FILE: x.py\nimport os\n")
    assert partial.endswith("```python\nimport os\n\n```") and "**New tab: x.py**" in partial


def test_compact_chat_hides_the_code():
    from viper_ide.assistantui import display_markdown

    out = display_markdown(NEW_FILE_REPLY, show_code=False, summary="SUMMARY")
    assert "NEW FILE" not in out and "SEARCH" not in out and "```" not in out and "mean(" not in out
    assert out.endswith("SUMMARY")
    fenced = "Here you go.\n\n```python\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n```\n\nDone."
    assert display_markdown(fenced, show_code=False) == "Here you go.\n\nDone."
    assert display_markdown("Ok.\n\n<<<<<<< SEARCH\nimport os\n", show_code=False) == "Ok.\n\n_Writing an edit..._"
    assert display_markdown("Ok.\n<<<<<<< NEW FILE: a.py\nx = 1\n", show_code=False).endswith("_Writing a.py..._")
    # Ordinary example code in an answer is still shown.
    assert "print(1)" in display_markdown("Try:\n\n```python\nprint(1)\n```", show_code=False)


def test_normalise_base():
    assert assistant.normalise_base("localhost:8080/v1/") == "http://localhost:8080/v1"
    assert assistant.normalise_base("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"


class FakeOpenAI(BaseHTTPRequestHandler):
    requests: list = []
    reply = REPLY
    stream = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "fake-coder"}, {"id": "fake-chat"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.requests.append({"auth": self.headers.get("Authorization"), "body": data,
                                    "port": self.server.server_address[1]})
        if not FakeOpenAI.stream:
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": FakeOpenAI.reply}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n\n')
        text = FakeOpenAI.reply
        for i in range(0, len(text), 7):
            chunk = {"choices": [{"delta": {"content": text[i:i + 7]}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def server():
    FakeOpenAI.requests = []
    FakeOpenAI.reply = REPLY
    FakeOpenAI.stream = True
    srv = _serve()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture
def server2():
    srv = _serve()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_legacy_settings_become_a_provider(tmp_path):
    from viper_ide.settings import Settings

    s = Settings(tmp_path / "s.json")
    s._data.update(ai_base_url="http://old/v1", ai_api_key="sk-old", ai_model="m1")
    p = assistant.active_provider(s)
    assert p["name"] == "Default" and p["base_url"] == "http://old/v1" and p["api_key"] == "sk-old"
    assert p["model"] == "m1" and "ai_base_url" not in s._data
    assert json.loads((tmp_path / "s.json").read_text())["ai_provider"] == "Default"


def test_keys_stay_with_their_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert assistant.api_key(assistant.new_provider("o", "u", "OPENAI_API_KEY")) == "sk-openai"
    assert assistant.api_key(assistant.new_provider("local", "http://localhost:11434/v1")) == ""
    assert assistant.api_key(assistant.new_provider("g", "u", "GROQ_API_KEY")) == ""
    assert assistant.api_key(dict(assistant.new_provider("g", "u", "OPENAI_API_KEY"), api_key="sk-own")) == "sk-own"


def test_client_streams_and_lists_models(server):
    assert assistant.list_models(server, "") == ["fake-chat", "fake-coder"]
    seen = []
    text = assistant.stream_chat(server, "sk-test", "fake-coder", [{"role": "user", "content": "hi"}],
                                 progress=seen.append)
    assert text == REPLY
    assert seen[0] is None and seen[-1] == REPLY  # reasoning first, then content
    req = FakeOpenAI.requests[-1]
    assert req["auth"] == "Bearer sk-test"
    assert req["body"]["model"] == "fake-coder" and req["body"]["stream"] is True


def test_client_handles_non_streaming_servers(server):
    FakeOpenAI.stream = False
    assert assistant.stream_chat(server, "", "m", [{"role": "user", "content": "hi"}]) == REPLY


def test_client_reports_unreachable_server():
    with pytest.raises(assistant.AssistantError):
        assistant.list_models("http://127.0.0.1:9/v1", "", timeout=2)


def test_assistant_dock_edits_the_file(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide import assistantui
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="fake-coder")],
                          ai_provider="Fake")
    src = tmp_path / "calc.py"
    src.write_text(SOURCE)
    win = MainWindow(settings, [str(src)])
    win.show()
    try:
        page = win.page()
        assert page and page.editor.path == str(src)
        win.a["ask_ai"].trigger()
        panel = win.assistant
        assert win.assistant_dock.isVisible() and panel.include.isChecked()
        panel.input.setPlainText("add type hints")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert panel.bar.isVisible() and panel.bar.tag == "edits"
        sent = FakeOpenAI.requests[-1]["body"]["messages"]
        assert sent[0]["role"] == "system" and "<file>" in sent[-1]["content"] and "add type hints" in sent[-1]["content"]

        monkeypatch.setattr(assistantui.DiffPreviewDialog, "exec", lambda self: 1)
        assert panel.review_edits()
        assert page.editor.text().startswith("def add(a: int, b: int) -> int:")
        assert page.editor.isModified()
        page.editor.undo()
        assert page.editor.text() == SOURCE  # the whole change is one undo step

        # The chat shows the explanation and a Review & Apply link, not the code.
        chat = panel.view.toPlainText()
        assert "SEARCH" not in chat and "applied" in chat and "Review again" in chat
        from PyQt6.QtCore import QUrl
        panel._link_clicked(QUrl(panel._link("review", 1)))
        assert page.editor.text().startswith("def add(a: int, b: int) -> int:")  # the link re-applies it
        page.editor.undo()
        panel.show_code.setChecked(True)
        assert "SEARCH" not in panel.view.toPlainText() and "def add(a: int" in panel.view.toPlainText()
        panel.show_code.setChecked(False)

        # Follow-up turns carry the history but only the newest message has the file.
        panel.input.setPlainText("thanks")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        sent = FakeOpenAI.requests[-1]["body"]["messages"]
        assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
        assert sent[1]["content"] == "add type hints" and "<file>" in sent[3]["content"]
    finally:
        for p in win.pages():
            p.editor.setModified(False)
        win.close()


def test_assistant_opens_new_files_in_tabs(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="fake-coder")],
                          ai_provider="Fake")
    src = tmp_path / "calc.py"
    src.write_text(SOURCE)
    win = MainWindow(settings, [str(src)])
    win.show()
    try:
        FakeOpenAI.reply = NEW_FILE_REPLY
        panel = win.assistant
        win.a["ask_ai"].trigger()
        panel.input.setPlainText("move mean into a new file")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert win.tabs.count() == 2
        new = win.editor()
        assert new.path is None and new.display_name() == "stats.py" and new.isModified()
        assert new.text() == "def mean(xs):\n    return sum(xs) / len(xs)\n"
        assert "stats.py" in win.tabs.tabText(win.tabs.currentIndex())
        # The SEARCH/REPLACE part still targets the file that was sent.
        assert panel.bar.tag == "edits" and panel._pending[0].path == str(src)

        # A revised version of the same file replaces the tab instead of opening another.
        FakeOpenAI.reply = "<<<<<<< NEW FILE: stats.py\ndef mean(xs):\n    return sum(xs) / max(len(xs), 1)\n>>>>>>> END\n"
        panel.input.setPlainText("guard against empty lists")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert win.tabs.count() == 2 and win.editor() is new and "max(len(xs), 1)" in new.text()
        new.undo()
        assert "max(" not in new.text()
    finally:
        for p in win.pages():
            p.editor.setModified(False)
        win.close()


def test_switch_provider_mid_conversation(app, server, server2, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.providersui import ProvidersDialog
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable], ai_providers=[
        dict(assistant.new_provider("One", server), model="model-one", api_key="sk-one"),
        dict(assistant.new_provider("Two", server2), model="model-two", api_key="sk-two")], ai_provider="One")
    win = MainWindow(settings, [str(tmp_path)])
    win.show()
    try:
        panel = win.assistant
        win.show_assistant()
        assert [panel.provider.itemText(i) for i in range(panel.provider.count())] == ["One", "Two"]
        assert panel.model.currentText() == "model-one"
        panel.input.setPlainText("hello")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        first = FakeOpenAI.requests[-1]
        assert first["port"] == int(server.rsplit(":", 1)[1].split("/")[0]) and first["auth"] == "Bearer sk-one"

        panel.switch_provider("Two")
        assert panel.model.currentText() == "model-two" and settings.get("ai_provider") == "Two"
        panel.input.setPlainText("again")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        second = FakeOpenAI.requests[-1]
        assert second["port"] == int(server2.rsplit(":", 1)[1].split("/")[0]) and second["auth"] == "Bearer sk-two"
        assert second["body"]["model"] == "model-two"
        assert [m["role"] for m in second["body"]["messages"]] == ["system", "user", "assistant", "user"]

        # Model picks are remembered per provider.
        panel.model.setCurrentText("model-two-b")
        panel._model_changed()
        panel.switch_provider("One")
        assert panel.model.currentText() == "model-one"
        assert assistant.providers(settings)[1]["model"] == "model-two-b"

        # The Code menu lists providers with the active one checked.
        win._fill_ai_provider_menu()
        checked = [a.text() for a in win.ai_provider_menu.actions() if a.isChecked()]
        assert checked == ["One  (model-one)"]

        # Manage dialog: add a preset, make it active, save.
        dlg = ProvidersDialog(settings, win)
        dlg._add("Custom", server2, "")
        dlg._make_active()
        dlg.accept()
        panel.providers_changed()
        assert settings.get("ai_provider") == "Custom"
        assert [p["name"] for p in assistant.providers(settings)] == ["One", "Two", "Custom"]
        assert panel.provider.currentText() == "Custom"
    finally:
        win.close()


def test_error_excerpt_and_context():
    out = "starting\nTraceback (most recent call last):\n  File \"a.py\", line 3, in <module>\n    1/0\n" \
          "ZeroDivisionError: division by zero\n"
    err = assistant.error_excerpt(out)
    assert err.startswith("Traceback") and err.endswith("ZeroDivisionError: division by zero")
    syntax = assistant.error_excerpt('  File "a.py", line 1\n    def (\n        ^\nSyntaxError: invalid syntax\n')
    assert syntax.startswith('  File "a.py"') and "SyntaxError" in syntax
    assert assistant.error_excerpt("all fine\n") is None
    assert len(assistant.error_excerpt(_TB_LONG)) <= assistant.MAX_ERROR_CHARS + 4
    msg = assistant.context_message("a.py", "x = y\n", None,
                                    [{"line": 1, "severity": "error", "message": "undefined name 'y'"}],
                                    [("Run panel", err + "\n"), ("Python console", ">>> \n>>>\n...\n"), ("Terminal", "")])
    assert "<problems>\nline 1: error: undefined name 'y'\n</problems>" in msg
    assert '<red_output source="Run panel">\n' + err + "\n</red_output>" in msg
    assert "Python console" not in msg and "Terminal" not in msg  # prompts only / empty: nothing to show
    assert assistant.clean_red("x" * 9000).startswith("...\n") and len(assistant.clean_red("x" * 9000)) < 4100


_TB_LONG = "Traceback (most recent call last):\n" + "  File \"a.py\", line 1, in f\n" * 1000 + "RecursionError: x\n"


def test_fix_errors_sends_traceback_and_problems(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="m")], ai_provider="Fake")
    src = tmp_path / "crash.py"
    src.write_text("import os\n\n\ndef ratio(a, b):\n    return a / b\n\n\nprint(ratio(1, 0))\n")
    win = MainWindow(settings, [str(src)])
    win.show()
    try:
        assert wait(app, lambda: win.interp is not None, 30)
        page = win.page()
        assert wait(app, lambda: page.editor.lint_items, 20)  # "'os' imported but unused"
        win.run_file(False)
        assert wait(app, lambda: page.info.isVisible() and page.info.tag == "run_error", 30)
        assert "ZeroDivisionError" in page.info.label.text()
        assert win.last_run_error and win.last_run_error[0] == str(src)

        FakeOpenAI.reply = "The divisor can be zero.\n\n<<<<<<< SEARCH\nprint(ratio(1, 0))\n=======\nprint(ratio(1, 1))\n>>>>>>> REPLACE\n"
        page.info.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QPushButton"]).QPushButton)[0].click()
        panel = win.assistant
        assert wait(app, lambda: FakeOpenAI.requests and not panel.busy(), 20)
        sent = FakeOpenAI.requests[-1]["body"]["messages"][-1]["content"]
        assert '<red_output source="Run panel: stderr of the last run of crash.py, which exited with code 1">' in sent
        assert "ZeroDivisionError: division by zero" in sent
        assert "<problems>" in sent and "imported but unused" in sent
        assert "failed with the error shown" in sent
        assert not page.info.isVisible() and panel.bar.tag == "edits"

        # With "Include errors" off, neither is sent.
        panel.include_errors.setChecked(False)
        panel.input.setPlainText("just look")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        sent = FakeOpenAI.requests[-1]["body"]["messages"][-1]["content"]
        assert "<red_output" not in sent and "<problems>" not in sent and "<file>" in sent
    finally:
        for p in win.pages():
            p.editor.setModified(False)
        win.close()


def test_red_output_from_everywhere_reaches_the_assistant(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="m")], ai_provider="Fake")
    src = tmp_path / "warn.py"
    src.write_text("import warnings\n\nwarnings.warn('old_api() is deprecated', DeprecationWarning, stacklevel=1)\n"
                   "print('done')\n")
    win = MainWindow(settings, [str(src)])
    win.show()
    try:
        assert wait(app, lambda: win.interp is not None, 30)
        page = win.page()
        win.run_file(False)
        assert wait(app, lambda: "exited with code 0" in win.run_panel.view.toPlainText(), 30)
        assert "old_api() is deprecated" in win.run_panel.view.red
        assert "exited" not in win.run_panel.view.red  # Viper's own status line isn't program output
        assert not page.info.isVisible()  # a warning isn't a crash: no Fix with AI bar
        assert win.last_run_error is None

        win.console.run_code("int('x')")
        assert wait(app, lambda: "invalid literal" in win.console.view.red, 30)
        page.info.show_message("error", "Couldn't install <b>foo</b>: no matching distribution", tag="t")

        panel = win.assistant
        win.show_assistant()
        panel.update_context()
        assert "red text from run, python console, notice" in panel.context_label.text()
        panel.fix_errors()
        assert wait(app, lambda: FakeOpenAI.requests and not panel.busy(), 20)
        sent = FakeOpenAI.requests[-1]["body"]["messages"][-1]["content"]
        assert "which finished successfully" in sent and "old_api() is deprecated" in sent
        assert '<red_output source="Python console">' in sent and "invalid literal for int()" in sent
        assert '<red_output source="Notice above the editor">' in sent and "Couldn't install foo: no matching" in sent
        assert "shown in red" in sent

        # Clearing a panel clears what the assistant sees from it; a new run starts fresh.
        win.console.view.clear()
        page.info.clear()
        src.write_text("print('clean')\n")
        page.editor.setText("print('clean')\n")
        win.run_file(False)
        assert wait(app, lambda: "exited with code 0" in win.run_panel.view.toPlainText()
                    and not win.run_panel.running(), 30)
        assert win.red_outputs() == []
    finally:
        for p in win.pages():
            p.editor.setModified(False)
        win.close()


def test_output_message_trims_to_the_end():
    out = assistant.output_message([("Terminal", "$ ls\n" + "x" * 50 + "END"), ("Python console", "  \n")], limit=20)
    assert '<panel_output source="Terminal">' in out and out.count("<panel_output") == 1
    assert "END" in out and "$ ls" not in out
    assert assistant.output_message([("Run panel", "")]) == ""


def test_terminal_output_reaches_the_assistant(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="m")], ai_provider="Fake")
    src = tmp_path / "hello.py"
    src.write_text("print('hello from the run')\n")
    win = MainWindow(settings, [str(src)])
    win.show()
    try:
        assert wait(app, lambda: win.interp is not None, 30)
        win.run_file(False)
        assert wait(app, lambda: "exited with code 0" in win.run_panel.view.toPlainText(), 30)
        win.terminal.input.setText("echo terminal-says-hi")
        win.terminal._submit()
        assert wait(app, lambda: win.terminal.view.toPlainText().count("terminal-says-hi") >= 2, 30)

        panel = win.assistant
        win.show_assistant()
        assert panel.include_output.isChecked()  # on by default
        panel.include.setChecked(False)  # terminal output goes along even without the file
        assert panel.context_label.text() == "output from run, terminal"
        panel.input.setPlainText("what happened?")
        panel.send()
        assert wait(app, lambda: FakeOpenAI.requests and not panel.busy(), 20)
        sent = FakeOpenAI.requests[-1]["body"]["messages"][-1]["content"]
        assert "<file>" not in sent and sent.endswith("what happened?")
        assert '<panel_output source="Run panel: output of the last run of hello.py">' in sent
        assert "hello from the run" in sent and "exited with code 0" in sent
        assert '<panel_output source="Terminal">' in sent and "echo terminal-says-hi" in sent

        panel.include_output.setChecked(False)
        assert settings.get("ai_include_output") is False and panel.context_label.text() == ""
        panel.input.setPlainText("again")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert "panel_output" not in FakeOpenAI.requests[-1]["body"]["messages"][-1]["content"]
    finally:
        win.terminal.stop()
        for p in win.pages():
            p.editor.setModified(False)
        win.close()


def test_chat_stays_scrolled_to_the_bottom(app, server, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[dict(assistant.new_provider("Fake", server), model="m")], ai_provider="Fake")
    FakeOpenAI.reply = "\n\n".join(f"Paragraph {i} of a long answer." for i in range(120))
    win = MainWindow(settings, [str(tmp_path)])
    win.resize(1200, 700)
    win.show()
    try:
        panel = win.assistant
        win.show_assistant()
        bar = panel.view.verticalScrollBar()
        at_bottom = lambda: bar.maximum() > 0 and bar.value() == bar.maximum()  # noqa: E731

        panel.input.setPlainText("tell me a lot")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert wait(app, at_bottom, 5), (bar.value(), bar.maximum())

        # Scrolled up to read: a new reply streaming in doesn't yank the view...
        bar.setValue(0)
        app.processEvents()
        panel._transcript.append(("note", "x"))
        panel._render()
        assert wait(app, lambda: True, 0.3) and bar.value() == 0
        # ...but sending a new message jumps back to the newest text.
        panel.input.setPlainText("more")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 20)
        assert wait(app, at_bottom, 5), (bar.value(), bar.maximum())
    finally:
        win.close()
