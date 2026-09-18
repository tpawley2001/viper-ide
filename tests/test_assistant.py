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
        FakeOpenAI.requests.append({"auth": self.headers.get("Authorization"), "body": data})
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


@pytest.fixture
def server():
    FakeOpenAI.requests = []
    FakeOpenAI.reply = REPLY
    FakeOpenAI.stream = True
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


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


def test_assistant_dock_edits_the_file(server, tmp_path, monkeypatch):
    from test_gui import wait
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide import assistantui
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable], ai_base_url=server,
                          ai_model="fake-coder")
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
