"""LineScope.

Author: Mavs
Description: Optional, bounded HTTP requests to user-selected AI providers.

"""

from __future__ import annotations

from enum import StrEnum
from http.client import HTTPException
from ipaddress import ip_address
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class Provider(StrEnum):
    """Select a supported inference protocol.

    Attributes
    ----------
    OPENAI : str
        OpenAI Responses API with bearer authentication.

    ANTHROPIC : str
        Claude Messages API with bearer authentication and API versioning.

    COMPATIBLE : str
        OpenAI-compatible Chat Completions API, including local models.

    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    COMPATIBLE = "compatible"


class ProviderError(ValueError):
    """Report an inference failure without exposing request secrets.

    Messages contain only controlled diagnostics, never provider bodies.

    """


class _NoRedirect(HTTPRedirectHandler):
    """Prevent credentials from following provider redirects.

    A redirect is surfaced as a controlled HTTP failure instead.

    """

    def redirect_request(
        self,
        req: Request,  # noqa: ARG002
        fp: Any,  # noqa: ARG002
        code: int,  # noqa: ARG002
        msg: str,  # noqa: ARG002
        headers: Any,  # noqa: ARG002
        newurl: str,  # noqa: ARG002
    ):
        """Decline a redirect without copying its authorization header.

        Parameters follow the standard-library redirect-handler protocol.

        Parameters
        ----------
        req : [Request]
            Original request, intentionally never copied.

        fp : Any
            Response stream owned by the opener.

        code : int
            Redirect status received from the provider.

        msg : str
            Untrusted provider status description, ignored.

        headers : Any
            Response headers, ignored by this policy.

        newurl : str
            Redirect destination that must not receive credentials.

        """
        return None


def _endpoint(value: str) -> str:
    """Validate a complete inference URL before attaching credentials.

    Permit unencrypted HTTP only for literal loopback addresses or localhost.
    Reject URL credentials and queries to keep secrets out of request URLs.

    Parameters
    ----------
    value : str
        Complete provider inference endpoint supplied by the user.

    Returns
    -------
    str
        Validated URL with surrounding whitespace removed.

    """
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ProviderError("Enter a valid provider endpoint URL.") from error

    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
        or (port is not None and port == 0)
    ):
        raise ProviderError("Use an endpoint URL without credentials, query, or fragment.")

    try:
        loopback = ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"

    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ProviderError("Provider endpoints require HTTPS, except on localhost.")

    return value


def _response_text(payload: Any, provider: Provider) -> str:
    """Extract only completed assistant text from a provider response.

    Preserve generated source text as text; callers must not render it as HTML.

    Parameters
    ----------
    payload : Any
        Decoded provider response, whose structure is not trusted.

    provider : Provider
        Protocol used for the request.

    Returns
    -------
    str
        Nonempty suggestion text returned by the model.

    """
    if not isinstance(payload, dict) or payload.get("error"):
        raise ProviderError("The provider returned an unsuccessful response.")

    texts: list[str] = []
    if provider == Provider.OPENAI:
        if payload.get("status") not in (None, "completed"):
            raise ProviderError("The provider did not complete the suggestion. Try again.")
        output = payload.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                texts.extend(
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                )
    elif provider == Provider.ANTHROPIC:
        if payload.get("stop_reason") not in ("end_turn", "stop_sequence"):
            raise ProviderError("The provider did not complete the suggestion. Try again.")
        content = payload.get("content", [])
        if isinstance(content, list):
            texts.extend(
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            )
    else:
        choices = payload.get("choices", [])
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            if choice.get("finish_reason") in ("length", "content_filter", "tool_calls"):
                raise ProviderError("The provider did not complete the suggestion. Try again.")
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                texts.append(message["content"])

    result = "\n".join(texts).strip()
    if not result:
        raise ProviderError("The provider returned no suggestion text.")
    return result


def request_suggestion(
    *,
    provider: str | Provider,
    model: str,
    prompt: str,
    endpoint: str = "",
    api_key: str = "",
) -> str:
    """Request a suggestion without persisting credentials or source.

    Send a single bounded request with a 60-second timeout. Environment keys
    are read only for this call. Redirects are refused, and provider response
    bodies are never copied into failure messages.

    Parameters
    ----------
    provider : str | Provider
        Inference protocol: `openai`, `anthropic`, or `compatible`.

    model : str
        Model identifier configured by the user for their provider.

    prompt : str
        Reviewed prompt containing the selected snapshot and measurements.

    endpoint : str, default=""
        Complete endpoint URL for a custom compatible API. Named connections
        accept only their preset endpoint or empty text.

    api_key : str, default=""
        Request-only bearer credential. Empty uses `OPENAI_API_KEY`,
        `ANTHROPIC_API_KEY`, or `LINESCOPE_AI_API_KEY` for the selected
        connection. Local compatible models may omit authentication.

    Returns
    -------
    str
        Suggestion text. No code is executed or applied.

    """
    try:
        selected = Provider(provider)
    except ValueError as error:
        raise ProviderError("Choose a supported API connection.") from error

    if not model.strip() or len(model) > 256:
        raise ProviderError("Enter a model identifier of at most 256 characters.")
    if not prompt.strip() or len(prompt.encode("utf-8")) > 500_000:
        raise ProviderError("Provide a nonempty prompt of at most 500 KB.")

    presets = {
        Provider.OPENAI: "https://api.openai.com/v1/responses",
        Provider.ANTHROPIC: "https://api.anthropic.com/v1/messages",
    }
    url = _endpoint(endpoint or presets.get(selected, ""))
    if selected in presets and url != presets[selected]:
        raise ProviderError(
            "This connection uses its preset endpoint. Choose Custom API for another URL."
        )
    environment_key = {
        Provider.OPENAI: "OPENAI_API_KEY",
        Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
        Provider.COMPATIBLE: "LINESCOPE_AI_API_KEY",
    }[selected]
    key = api_key.strip() or os.environ.get(environment_key, "").strip()
    if selected in presets and not key:
        raise ProviderError(f"Enter an API key or set {environment_key} before serving.")
    if len(key) > 8192 or any(ord(character) < 32 or ord(character) > 126 for character in key):
        raise ProviderError("The API key must contain at most 8192 printable ASCII characters.")

    body: dict[str, Any] = {"model": model.strip()}
    if selected == Provider.OPENAI:
        body.update({"input": prompt, "store": False})
    else:
        body["messages"] = [{"role": "user", "content": prompt}]
        body["stream"] = False
        if selected == Provider.ANTHROPIC:
            body["max_tokens"] = 4096
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if selected == Provider.ANTHROPIC:
        headers["anthropic-version"] = "2023-06-01"
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

    try:
        with build_opener(_NoRedirect()).open(request, timeout=60) as response:
            raw = response.read(1_000_001)
    except HTTPError as error:
        status = error.code
        error.close()
        raise ProviderError(
            f"The provider returned HTTP {status}. Check the endpoint, key, model, and quota."
        ) from None
    except (URLError, TimeoutError, OSError, ValueError, HTTPException):
        raise ProviderError(
            "The provider could not be reached. Check the endpoint and connection."
        ) from None

    if len(raw) > 1_000_000:
        raise ProviderError("The provider response exceeded the 1 MB limit.")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("The provider returned invalid JSON.") from None
    suggestion = _response_text(payload, selected)
    return suggestion.replace(key, "[redacted credential]") if key else suggestion
