"""Gemini connectivity scripts must not embed a Google API key.

The scripts under test call the Gemini API. These tests execute them with
network sends blocked and check the credential the installed SDKs attach to
those calls. They fail while a key is baked into the source, and pass once
every call uses only the GEMINI_API_KEY environment variable.

Run:

    pip install 'google-generativeai==0.3.2' 'google-genai>=0.2.0' pytest==7.4.3
    python -m pytest form-genome-structured-cloudrun/tests/test_no_hardcoded_gemini_api_key.py -q
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "form-genome-structured-cloudrun"
CONNECTIVITY_SCRIPTS = (
    BACKEND_ROOT / "test_gemini.py",
    BACKEND_ROOT / "test_gemini_25.py",
)
# Google API keys are 39 characters and start with this prefix.
GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z\-_]{35}")
PLACEHOLDER_KEY = "placeholder-gemini-key"
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
}

# Executes a connectivity script in a child interpreter. Socket connects are
# blocked, and the HTTP / gRPC call sites the installed SDKs actually use are
# recorded and then stopped. Captured records never include credential values.
RUNNER = r"""
import json
import os
import runpy
import socket
import sys

def _blocked_connect(self, address):
    raise OSError("network blocked")

socket.socket.connect = _blocked_connect

def _blocked_create_connection(*args, **kwargs):
    raise OSError("network blocked")

socket.create_connection = _blocked_create_connection

captured = []
current_model = []

def _note(url, header_value, method):
    import re
    pattern = re.compile(r"^AIza[0-9A-Za-z\-_]{35}$")
    env_key = os.environ.get("GEMINI_API_KEY")
    model = current_model[-1] if current_model else ""
    if model and model not in str(url):
        url = f"{url} model={model}"
    captured.append({
        "url": str(url),
        "method": str(method),
        "header_present": bool(header_value),
        "matches_google_api_key_shape": bool(header_value and pattern.fullmatch(str(header_value))),
        "equals_env_gemini_api_key": env_key is not None and header_value == env_key,
    })

try:
    import httpx
    def _send(self, request, *args, **kwargs):
        _note(request.url, request.headers.get("x-goog-api-key"), request.method)
        raise RuntimeError("network blocked")
    httpx.Client.send = _send
except ModuleNotFoundError:
    pass

try:
    import httpx2
    def _send2(self, request, *args, **kwargs):
        _note(request.url, request.headers.get("x-goog-api-key"), request.method)
        body = (
            '{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},'
            '"finishReason":"STOP"}]}'
        )
        return httpx2.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json"},
        )
    httpx2.Client.send = _send2
except ModuleNotFoundError:
    pass

import google.auth.transport.grpc as gat_grpc
_ORIG_HEADERS = gat_grpc.AuthMetadataPlugin._get_authorization_headers

def _get_headers(self, context):
    headers = _ORIG_HEADERS(self, context)
    key = None
    for name, value in headers:
        if str(name).lower() == "x-goog-api-key":
            key = value
    method = getattr(context, "method_name", "")
    service = getattr(context, "service_url", "")
    _note(f"{service} {method}", key, "grpc")
    raise RuntimeError("network blocked")

gat_grpc.AuthMetadataPlugin._get_authorization_headers = _get_headers

import google.api_core.retry.retry_unary as retry_unary

def _retry_once(target, predicate, sleep_generator, timeout=None, on_error=None, exception_factory=None, **kwargs):
    return target()

retry_unary.retry_target = _retry_once

import requests

def _session_request(self, method, url, **kwargs):
    headers = kwargs.get("headers") or {}
    key = headers.get("x-goog-api-key") if hasattr(headers, "get") else None
    _note(url, key, method)
    raise RuntimeError("network blocked")

requests.Session.request = _session_request

import google.generativeai as genai_old

_real_list_models = genai_old.list_models

def _list_models(*args, **kwargs):
    try:
        return iter(list(_real_list_models(*args, **kwargs)))
    except Exception:
        return iter(())

genai_old.list_models = _list_models

_real_generate = genai_old.GenerativeModel.generate_content

def _generate(self, *args, **kwargs):
    current_model.append(getattr(self, "model_name", "") or "")
    try:
        return _real_generate(self, *args, **kwargs)
    finally:
        current_model.pop()

genai_old.GenerativeModel.generate_content = _generate

script = sys.argv[1]
out = sys.argv[2]
try:
    runpy.run_path(script, run_name="__main__")
except BaseException:
    pass
with open(out, "w", encoding="utf-8") as handle:
    json.dump(captured, handle)
"""


def _child_env(gemini_api_key: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env["PYTHONUNBUFFERED"] = "1"
    if gemini_api_key is not None:
        env["GEMINI_API_KEY"] = gemini_api_key
    return env


def _run_script(script: Path, gemini_api_key: str | None) -> list[dict]:
    handle = tempfile.NamedTemporaryFile(prefix="gemini-capture-", suffix=".json", delete=False)
    output = Path(handle.name)
    handle.close()
    try:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", RUNNER, str(script), str(output)],
                cwd=str(script.parent),
                env=_child_env(gemini_api_key),
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                f"{script.name} did not finish after network sends were blocked"
            ) from exc
        if not output.is_file():
            raise AssertionError(f"{script.name} did not record Gemini calls")
        payload = json.loads(output.read_text(encoding="utf-8") or "[]")
    finally:
        output.unlink(missing_ok=True)
    if completed.returncode not in (0,):
        raise AssertionError(f"{script.name} runner exited {completed.returncode}")
    if not isinstance(payload, list):
        raise AssertionError(f"{script.name} capture was not a list")
    return payload


def _credential_problem(script: Path, captured: list[dict]) -> str | None:
    for item in captured:
        if not item.get("header_present"):
            continue
        if not item.get("equals_env_gemini_api_key"):
            return (
                f"{script.name} sent {item.get('method')} {item.get('url')} "
                "with a credential other than GEMINI_API_KEY"
            )
    return None


def _generate_content_models(script: Path) -> list[str]:
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    models: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "GenerativeModel" and node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                models.append(node.args[0].value)
        if name != "generate_content":
            continue
        for keyword in node.keywords:
            if keyword.arg == "model" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    models.append(keyword.value.value)
    return models


def _calls_generate_content(script: Path) -> bool:
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "generate_content":
                return True
    return False


def _iter_source_files():
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        if path.stat().st_size > 2_000_000:
            continue
        yield path


def test_repository_source_has_no_google_api_key_literals():
    """A readable Google API key string in the tree is itself the leak."""
    hits: list[str] = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if GOOGLE_API_KEY_RE.search(line):
                relative = path.relative_to(REPO_ROOT)
                hits.append(f"{relative}:{line_number}")
    assert hits == [], "Google API key literal at " + ", ".join(hits)


@pytest.mark.parametrize("script", CONNECTIVITY_SCRIPTS, ids=lambda path: path.name)
def test_connectivity_script_uses_only_environment_key_for_generate_content(script: Path):
    """Each generateContent call must authenticate with GEMINI_API_KEY alone."""
    if not script.exists():
        pytest.skip(f"{script.name} is not in the tree")
    captured = _run_script(script, PLACEHOLDER_KEY)
    problem = _credential_problem(script, captured)
    assert problem is None, problem
    if _calls_generate_content(script):
        urls = " ".join(str(item.get("url", "")) for item in captured)
        assert "generateContent" in urls or "GenerateContent" in urls, (
            f"{script.name} calls generate_content but no generateContent request was built"
        )
        missing = [model for model in _generate_content_models(script) if model not in urls]
        assert missing == [], (
            f"{script.name} did not build a Gemini request for {', '.join(missing)}"
        )


@pytest.mark.parametrize("script", CONNECTIVITY_SCRIPTS, ids=lambda path: path.name)
def test_connectivity_script_sends_no_google_api_key_when_environment_is_unset(script: Path):
    """An unset GEMINI_API_KEY must not fall back to a key stored in the script."""
    if not script.exists():
        pytest.skip(f"{script.name} is not in the tree")
    captured = _run_script(script, None)
    leaked = [
        f"{item.get('method')} {item.get('url')}"
        for item in captured
        if item.get("matches_google_api_key_shape")
    ]
    assert leaked == [], (
        f"{script.name} attached a Google API key while GEMINI_API_KEY was unset: "
        + ", ".join(leaked)
    )


def test_environment_sourced_script_sends_only_the_placeholder(tmp_path: Path):
    """The harness accepts a script that reads GEMINI_API_KEY and calls both SDKs."""
    script = tmp_path / "fixed_gemini_connectivity.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import google.generativeai as genai_old
            from google import genai
            from google.genai import types

            api_key = os.getenv("GEMINI_API_KEY")
            try:
                genai_old.configure(api_key=api_key)
                for _model in genai_old.list_models():
                    break
                model = genai_old.GenerativeModel("gemini-1.5-flash")
                model.generate_content("Hello")
            except Exception:
                pass
            client = genai.Client(api_key=api_key)
            client.models.generate_content(
                model="gemini-2.5-flash",
                contents="Hello",
                config=types.GenerateContentConfig(max_output_tokens=10),
            )
            """
        ).lstrip(),
        encoding="utf-8",
    )
    captured = _run_script(script, PLACEHOLDER_KEY)
    problem = _credential_problem(script, captured)
    assert problem is None, problem
    urls = " ".join(str(item.get("url", "")) for item in captured)
    assert "GenerateContent" in urls
    assert "gemini-2.5-flash:generateContent" in urls
    assert captured, "environment-sourced script did not build a Gemini request"
