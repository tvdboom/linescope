"""LineScope.

Author: Mavs
Description: Serve one captured report and optional AI requests on loopback.

"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
from threading import Event, Lock
import time
from typing import Any, cast
import webbrowser

import click

from linescope.ai.connections import (
    Connection,
    configured_agent,
    credential_environment,
    describe_connections,
)
from linescope.ai.providers import ProviderError, request_suggestion


class _ContextParser(HTMLParser):
    """Read the embedded context without executing report content.

    Attributes
    ----------
    collecting : bool
        Whether text belongs to the uniquely identified JSON script element.

    chunks : list[str]
        Raw JSON fragments collected from that element.

    count : int
        Number of matching script elements, used to reject ambiguity.

    """

    def __init__(self):
        """Initialize an empty report context capture.

        HTML script data remains raw so JSON escape sequences are preserved.

        """
        super().__init__(convert_charrefs=False)
        self.collecting = False
        self.chunks: list[str] = []
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        """Start capturing the report's JSON script element.

        Match both its identifier and MIME type to reject unrelated scripts.

        Parameters
        ----------
        tag : str
            Parsed element name in lowercase.

        attrs : list[tuple[str, str | None]]
            Attribute pairs supplied by the HTML parser.

        """
        attributes = dict(attrs)
        if (
            tag == "script"
            and attributes.get("id") == "linescope-ai-context"
            and attributes.get("type") == "application/json"
        ):
            self.count += 1
            self.collecting = True

    def handle_endtag(self, tag: str):
        """Stop collecting at the script's closing tag.

        Other report elements do not change the captured payload.

        Parameters
        ----------
        tag : str
            Closing element name in lowercase.

        """
        if tag == "script":
            self.collecting = False

    def handle_data(self, data: str):
        """Retain text only inside the embedded context element.

        Source and metadata outside that element remain report-only content.

        Parameters
        ----------
        data : str
            Raw text between HTML tags.

        """
        if self.collecting:
            self.chunks.append(data)


def _read_context(report: str) -> dict[str, Any]:
    """Decode a single context payload from a saved report.

    Parameters
    ----------
    report : str
        Complete saved HTML report from this LineScope version.

    Returns
    -------
    dict[str, Any]
        Snapshot context stored in the report, without reading source files.

    """
    parser = _ContextParser()
    parser.feed(report)
    parser.close()
    if parser.count != 1:
        raise ValueError("Regenerate this report with AI context before using the local viewer.")
    try:
        payload = json.loads("".join(parser.chunks))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(
            "The report contains invalid AI context. Regenerate the report."
        ) from None
    if not isinstance(payload, dict):
        raise ValueError("The report contains invalid AI context. Regenerate the report.")
    return payload


class _ReportServer(ThreadingHTTPServer):
    """Own one report and its ephemeral local-viewer authorization token.

    Attributes
    ----------
    daemon_threads : bool
        Wait for bounded inference workers to finish during shutdown.

    context : dict[str, Any]
        Captured report data used to construct prompts without filesystem reads.

    token : str
        Random session credential; never saved back to the report file.

    authority : str
        Exact loopback host and bound port accepted in the Host header.

    origin : str
        Same-origin HTTP URL accepted for browser API requests.

    report : bytes
        UTF-8 HTML with ephemeral capability metadata injected into its head.

    agent_command : tuple[str, ...]
        Local command explicitly configured by the viewer owner. Browser
        requests cannot replace or extend it.

    cancel_event : [Event]
        Signal active local agents to stop before server threads are joined.

    inference_lock : [Lock]
        Allow only one inference request at a time to avoid duplicate work.

    """

    daemon_threads = False

    def __init__(self, report: str, port: int, agent_command: tuple[str, ...] = ()):
        """Bind a private viewer and inject its session capabilities.

        Validate the saved context before opening the listening socket.

        Parameters
        ----------
        report : str
            Saved self-contained HTML to serve.

        port : int
            Loopback port; zero requests an available operating-system port.

        agent_command : tuple[str, ...], default=()
            Explicit local agent command; empty disables local agents.

        """
        self.context = _read_context(report)
        self.token = secrets.token_urlsafe(32)
        self.agent_command = agent_command
        self.cancel_event = Event()
        self.inference_lock = Lock()
        super().__init__(("127.0.0.1", port), _ReportHandler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.authority}"
        connections = escape(json.dumps(describe_connections(agent_command)), quote=True)
        metadata = (
            f'<meta name="linescope-ai-token" content="{escape(self.token, quote=True)}">'
            f'<meta name="linescope-ai-origin" content="{escape(self.origin, quote=True)}">'
            f'<meta name="linescope-ai-connections" content="{connections}">'
            '<meta name="linescope-ai-agent" content="'
            f'{"available" if agent_command else ""}">'
        )
        # Keep UTF-8 detection early and fresh capabilities before saved ones.
        charset = '<meta charset="utf-8">'
        charset_start = report.find(charset, 0, report.find("</head>"))
        insertion = (
            charset_start + len(charset) if charset_start >= 0 else report.find("<head>") + 6
        )
        self.report = (report[:insertion] + metadata + report[insertion:]).encode("utf-8")

    def server_close(self):
        """Cancel active local agents before joining request workers.

        Provider HTTP requests finish within their existing timeout. Closing
        the viewer cannot leave agent subprocesses running without an owner.

        """
        self.cancel_event.set()
        super().server_close()


class _ReportHandler(BaseHTTPRequestHandler):
    """Handle authenticated requests without exposing unrelated files.

    Every request checks its literal Host header. Mutating requests also use
    a session token and reject a browser Origin outside this viewer.

    """

    def setup(self):
        """Bound request reads before parsing incoming headers or bodies.

        A stalled local client must not retain a worker indefinitely.

        """
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format: str, *args: Any):  # noqa: A002
        """Suppress request logging to keep paths and credentials private.

        The parameter spelling follows the HTTP handler protocol.

        Parameters
        ----------
        format : str
            Ignored logging template supplied by the HTTP handler.

        *args : Any
            Ignored logging values that may contain private request data.

        """

    def _reply(self, status: int, body: bytes, content_type: str):
        """Write a bounded response with no caching or embedding.

        Ignore disconnected clients after a request completes; source and
        provider failures must not become server traceback output.

        Parameters
        ----------
        status : int
            HTTP response status.

        body : bytes
            Already encoded report or JSON response body.

        content_type : str
            MIME type and character encoding of the response body.

        """
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            pass

    def _json(self, status: int, payload: dict[str, str]):
        """Return a controlled JSON result or diagnostic.

        Error payloads never contain upstream response bodies or credentials.

        Parameters
        ----------
        status : int
            HTTP response status.

        payload : dict[str, str]
            Suggestion, prompt, or controlled error text to serialize.

        """
        self._reply(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
        if status >= 400:
            self._finish_rejection()

    def _finish_rejection(self):
        """Deliver an error before closing over unread request bytes.

        Windows can reset a socket closed while a client is still sending
        a rejected body, losing the response. Half-close output first, then
        discard at most one MB for at most half a second. Authentication
        remains before body parsing, and no rejected input is dispatched.

        """
        deadline = time.monotonic() + 0.5
        remaining = 1_000_000
        try:
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            while remaining > 0:
                wait = deadline - time.monotonic()
                if wait <= 0:
                    break
                self.connection.settimeout(wait)
                discarded = self.rfile.read1(min(65_536, remaining))
                if not discarded:
                    break
                remaining -= len(discarded)
        except OSError:
            # A disconnected or stalled caller no longer needs its response.
            pass

    def _authorized(self, *, mutation: bool) -> bool:
        """Reject rebinding, foreign origins, and missing session tokens.

        Parameters
        ----------
        mutation : bool
            Require the session credential for a prompt or inference request.

        Returns
        -------
        bool
            Whether request processing may continue.

        """
        server = cast(_ReportServer, self.server)
        if self.headers.get_all("Host") != [server.authority]:
            self._json(403, {"error": "This viewer accepts only its loopback address."})
            return False
        origins = self.headers.get_all("Origin")
        if origins is not None and origins != [server.origin]:
            self._json(403, {"error": "Requests must come from this report viewer."})
            return False
        tokens = self.headers.get_all("X-LineScope-Token")
        if mutation and (
            tokens is None
            or len(tokens) != 1
            or not secrets.compare_digest(tokens[0].encode("utf-8"), server.token.encode("utf-8"))
        ):
            self._json(403, {"error": "Reload the local report to refresh its session."})
            return False
        return True

    def do_GET(self):
        """Serve only the loaded report at the viewer root.

        Arbitrary filesystem paths and query strings have no routes.

        """
        if not self._authorized(mutation=False):
            return
        if self.path != "/":
            self._json(404, {"error": "Not found."})
            return
        self._reply(200, cast(_ReportServer, self.server).report, "text/html; charset=utf-8")

    def do_POST(self):
        """Build a prompt or request suggestions after explicit user action.

        Browser requests cannot read other files, select executable commands,
        or retrieve the environment's credentials.

        """
        if not self._authorized(mutation=True):
            return
        if self.path not in ("/api/prompt", "/api/suggest"):
            self._json(404, {"error": "Not found."})
            return
        if (
            self.headers.get_content_type() != "application/json"
            or self.headers.get("Transfer-Encoding") is not None
            or len(self.headers.get_all("Content-Length", [])) != 1
        ):
            self._json(400, {"error": "Send a length-delimited JSON request."})
            return
        try:
            size = int(self.headers["Content-Length"])
        except (ValueError, TypeError):
            self._json(400, {"error": "Invalid request size."})
            return
        if not 0 < size <= 1_000_000:
            self._json(413, {"error": "The request exceeds the 1 MB limit."})
            return
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                self._json(400, {"error": "The request body was incomplete."})
                return
            payload = json.loads(raw)
        except (ValueError, UnicodeError, TimeoutError, ConnectionError, RecursionError):
            self._json(400, {"error": "The request body must be valid JSON."})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "The request must be a JSON object."})
            return
        try:
            result = (
                self._prompt(payload) if self.path == "/api/prompt" else self._suggest(payload)
            )
        except ProviderError as error:
            self._json(502, {"error": str(error)})
            return
        except (ValueError, TypeError, KeyError):
            self._json(
                400, {"error": "Invalid AI settings or source selection. Review the fields."}
            )
            return
        self._json(200, result)

    def _prompt(self, payload: dict[str, Any]) -> dict[str, str]:
        """Build a prompt from the viewer's captured report.

        Only the selected line and user-entered objective come from the request.

        Parameters
        ----------
        payload : dict[str, Any]
            Untrusted source selection and optimization settings.

        Returns
        -------
        dict[str, str]
            Constructed prompt ready for review in the report UI.

        """
        from linescope.ai.context import build_prompt

        context = cast(_ReportServer, self.server).context
        source_id = payload["source_id"]
        if isinstance(source_id, str):
            source_id = context.get("source_anchors", {}).get(source_id, source_id)
        prompt = build_prompt(
            context,
            source_id,
            payload["line"],
            goal=payload.get("goal", "both"),
            problem=payload.get("problem", ""),
            include_related=payload.get("include_related", True),
        )
        return {"prompt": prompt}

    def _suggest(self, payload: dict[str, Any]) -> dict[str, str]:
        """Dispatch one reviewed prompt using a supported connection.

        Local agents are selected by the server owner, never by the browser.

        Parameters
        ----------
        payload : dict[str, Any]
            Reviewed prompt and request-scoped connection settings.

        Returns
        -------
        dict[str, str]
            Completed model or agent suggestion as untrusted plain text.

        """
        values = {
            name: payload.get(name, "")
            for name in ("provider", "model", "prompt", "endpoint", "api_key")
        }
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("Invalid settings")
        server = cast(_ReportServer, self.server)
        if not server.inference_lock.acquire(blocking=False):
            raise ProviderError(
                "A suggestion is already running. Wait for it to finish before retrying."
            )
        try:
            if server.cancel_event.is_set():
                raise ProviderError("The local viewer is shutting down.")
            if values["provider"] in (
                Connection.AGENT,
                Connection.CLAUDE,
                Connection.CODEX,
                Connection.COPILOT,
            ):
                from linescope.ai.agents import request_agent_suggestion

                command = server.agent_command
                selected_agent = configured_agent(command)
                if selected_agent is None:
                    raise ProviderError(
                        "Restart the viewer with --ai-agent or --ai-command to use a local agent."
                    )
                if values["provider"] not in (Connection.AGENT, selected_agent):
                    raise ProviderError(
                        "This agent is not configured. Restart the viewer with the chosen agent."
                    )
                agent_options: dict[str, Any] = {"cancel_event": server.cancel_event}
                if values["api_key"].strip():
                    agent_options.update(
                        api_key=values["api_key"],
                        credential_env=credential_environment(selected_agent),
                    )
                if values["model"].strip():
                    agent_options["model"] = values["model"]
                try:
                    suggestion = request_agent_suggestion(
                        values["prompt"], command, **agent_options
                    )
                except ValueError as error:
                    raise ProviderError(str(error)) from None
                except OSError:
                    raise ProviderError(
                        "The local agent could not access its temporary working files."
                    ) from None
            else:
                suggestion = request_suggestion(**values)
        finally:
            server.inference_lock.release()
        return {"suggestion": suggestion}


def serve_report(
    path: str | Path,
    *,
    port: int = 0,
    open_browser: bool = True,
    agent_command: tuple[str, ...] = (),
):
    """Open a saved report with optional AI actions on the local computer.

    Keep the viewer running until interrupted. Bind only to IPv4 loopback,
    read no source files beyond the saved report, and close the server on
    normal interruption or startup failure. Credentials are never written
    into the report or returned by the server.

    Parameters
    ----------
    path : str | [Path]
        Saved LineScope HTML report containing captured AI context.

    port : int, default=0
        Loopback port. Zero lets the operating system choose a free port.

    open_browser : bool, default=True
        Open the viewer URL in the default browser after binding.

    agent_command : tuple[str, ...], default=()
        Explicitly configured local agent executable and arguments. Empty
        disables the local-agent connection.

    """
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 through 65535")
    report = Path(path).expanduser().read_text(encoding="utf-8")
    with _ReportServer(report, port, agent_command) as server:
        url = f"{server.origin}/"
        click.echo(f"LineScope viewer: {url}\nPress Ctrl+C to stop.", err=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
