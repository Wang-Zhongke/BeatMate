"""DeepSeek Responses API adapter. No sessions, tool execution, retries or fallback."""

import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .llm import OpenAIPlanner, PlannerError, strict_json


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the Authorization header to another URL.
        return None


class DeepSeekPlanner(OpenAIPlanner):
    provider = "deepseek"

    def __init__(self, *, environ=None, transport=None):
        env = os.environ if environ is None else environ
        key = env.get("DEEPSEEK_API_KEY", "")
        model = env.get("DEEPSEEK_MODEL", "deepseek-flash")
        base_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
            raise ValueError("Set a valid DEEPSEEK_API_KEY in the backend environment")
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model
        ):
            raise ValueError("Set a valid DEEPSEEK_MODEL")
        try:
            url = urllib.parse.urlsplit(base_url)
            valid = (
                url.scheme == "https"
                and url.hostname
                and url.port != 0
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and not any(c.isspace() for c in base_url)
                and not url.path.rstrip("/").endswith("/responses")
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError(
                "DEEPSEEK_BASE_URL must be an HTTPS base URL without credentials, query or fragment"
            )
        # With the official base this is /responses, not the OpenAI /v1/responses.
        self.endpoint = base_url.rstrip("/") + "/responses"
        super().__init__(key, model, transport)

    def _payload(self, text, instructions, output_schema):
        payload = super()._payload(text, instructions, output_schema)
        # Official compatibility table: store unsupported; API is stateless.
        del payload["store"]
        # Flash defaults to high reasoning; this bounded extraction task needs
        # its 2000-token budget for the JSON result, not hidden reasoning.
        payload["reasoning"] = {"effort": "none"}
        return payload

    def _request(self, payload):
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=45) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise PlannerError("DeepSeek response exceeds size limit")
                return strict_json(raw)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in (401, 403):
                raise PlannerError(
                    "DeepSeek authentication/permission failed", code="authentication"
                ) from None
            if status == 429:
                raise PlannerError(
                    "DeepSeek rate limit reached", code="rate_limit"
                ) from None
            if status in (400, 402, 404, 422):
                raise PlannerError(
                    "DeepSeek rejected configuration, request or account balance",
                    code="request_rejected",
                ) from None
            raise PlannerError(
                "DeepSeek HTTP request failed", code="http_error"
            ) from None
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ):
            raise PlannerError(
                "DeepSeek network unavailable or request timed out",
                code="network_unavailable",
            ) from None
        except (ValueError, TypeError, UnicodeError):
            # Do not echo provider response bodies, URLs or exception chains.
            raise PlannerError("Malformed DeepSeek response") from None
