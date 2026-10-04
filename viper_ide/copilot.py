"""GitHub Copilot Chat without an API key: sign in with GitHub, chat on your Copilot plan.

Sign-in is GitHub's device flow (the same one the Copilot editor plugins use): Viper
shows a short code, you enter it at github.com/login/device, and GitHub hands back an
account token. That token is traded for a short-lived Copilot session token, and the
session token talks to Copilot's chat endpoint, which speaks the OpenAI chat format,
so the rest of the assistant works unchanged.

Any GitHub account with Copilot works, including the free Copilot Free plan
(github.com/settings/copilot).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

# The public OAuth app id the Copilot editor plugins sign in with; Copilot only issues
# session tokens to accounts signed in through a Copilot-enabled app.
CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
SESSION_URL = "https://api.github.com/copilot_internal/v2/token"
USER_URL = "https://api.github.com/user"
DEFAULT_API = "https://api.githubcopilot.com"
COPILOT_SETTINGS_URL = "https://github.com/settings/copilot"

# Copilot rejects chat requests that don't identify an editor integration.
CHAT_HEADERS = {
    "Editor-Version": "vscode/1.99.0",
    "Editor-Plugin-Version": f"viper-ide/{__version__}",
    "Copilot-Integration-Id": "vscode-chat",
    "Openai-Intent": "conversation-panel",
}


class CopilotError(Exception):
    pass


def _call(url: str, data: dict | None = None, token: str = "", scheme: str = "token", timeout: float = 15.0):
    headers = {"Accept": "application/json", "User-Agent": f"ViperIDE/{__version__}"}
    if token:
        headers["Authorization"] = f"{scheme} {token}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("message") or e.reason
        except (OSError, ValueError, AttributeError):
            detail = e.reason
        raise CopilotError(_explain(e.code, detail)) from None
    except (OSError, ValueError) as e:
        raise CopilotError(f"Couldn't reach GitHub: {e}") from None


def _explain(code: int, detail: str) -> str:
    if code == 401:
        return "GitHub sign-in expired or was revoked. Click \"Sign in with GitHub\" again."
    if code in (403, 404):
        return ("This GitHub account doesn't have Copilot. Turn on Copilot Free (or a paid plan) at "
                f"{COPILOT_SETTINGS_URL}, then try again.")
    return f"GitHub HTTP {code}: {detail}"


# ------------------------------------------------------------------ sign-in
def start_sign_in() -> dict:
    """Begin the device flow: {user_code, verification_uri, device_code, interval, expires_in}."""
    data = _call(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": "read:user"})
    if "device_code" not in data:
        raise CopilotError(f"GitHub didn't start sign-in: {data}")
    return data


def finish_sign_in(flow: dict, cancel: threading.Event | None = None) -> str:
    """Wait until the user enters the code; returns the GitHub account token."""
    interval = max(int(flow.get("interval") or 5), 1)
    deadline = time.monotonic() + int(flow.get("expires_in") or 900)
    while time.monotonic() < deadline:
        if cancel is not None and cancel.wait(interval):
            raise CopilotError("Sign-in cancelled.")
        if cancel is None:
            time.sleep(interval)
        data = _call(ACCESS_TOKEN_URL, {"client_id": CLIENT_ID, "device_code": flow["device_code"],
                                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if data.get("access_token"):
            return data["access_token"]
        err = data.get("error")
        if err == "slow_down":
            interval = int(data.get("interval") or interval + 5)
        elif err != "authorization_pending":
            raise CopilotError({"expired_token": "The sign-in code expired. Try again.",
                                "access_denied": "Sign-in was declined on GitHub."}.get(
                err, data.get("error_description") or str(err)))
    raise CopilotError("The sign-in code expired. Try again.")


def account_name(github_token: str) -> str:
    return _call(USER_URL, token=github_token).get("login") or ""


# ------------------------------------------------------------------ session
_sessions: dict[str, tuple[str, str, float]] = {}  # github token -> (api base, session token, expires)
_lock = threading.Lock()


def session(github_token: str) -> tuple[str, str]:
    """(chat API base, session token) for this account, refreshed shortly before it expires."""
    if not github_token:
        raise CopilotError("Not signed in to GitHub. Open Manage Providers and click \"Sign in with GitHub\".")
    with _lock:
        cached = _sessions.get(github_token)
        if cached and cached[2] - 120 > time.time():
            return cached[0], cached[1]
    data = _call(SESSION_URL, token=github_token)
    token = data.get("token")
    if not token:
        raise CopilotError(f"GitHub didn't issue a Copilot token: {data.get('message') or data}")
    api = ((data.get("endpoints") or {}).get("api") or DEFAULT_API).rstrip("/")
    expires = float(data.get("expires_at") or time.time() + 1500)
    with _lock:
        _sessions[github_token] = (api, token, expires)
    return api, token


def forget(github_token: str) -> None:
    with _lock:
        _sessions.pop(github_token, None)


def chat_models(data) -> list[str]:
    """The chat models from Copilot's /models listing (it also lists embedding models)."""
    items = data.get("data", []) if isinstance(data, dict) else data or []
    out = []
    for m in items:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        kind = (m.get("capabilities") or {}).get("type")
        if kind not in (None, "chat") or m.get("model_picker_enabled") is False:
            continue
        out.append(m["id"])
    return sorted(set(out), key=str.lower)
