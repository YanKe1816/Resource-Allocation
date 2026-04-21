#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Resource Allocation"


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _text_response(handler: BaseHTTPRequestHandler, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
    data = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _load_html(filename: str) -> str:
    return (BASE_DIR / filename).read_text(encoding="utf-8")


def _validate_options(options):
    if not isinstance(options, list) or not options:
        return "'options' must be a non-empty array"
    for idx, item in enumerate(options):
        if not isinstance(item, dict):
            return f"options[{idx}] must be an object"
        if "name" not in item or "return" not in item:
            return f"options[{idx}] must include 'name' and 'return'"
        if not isinstance(item["name"], str) or not item["name"].strip():
            return f"options[{idx}].name must be a non-empty string"
        if not isinstance(item["return"], (int, float)):
            return f"options[{idx}].return must be a number"
    return None


def _allocate(total_resource, options):
    # Under a linear return assumption, maximizing total return means allocating all
    # resources to the option with the highest return (deterministic tie-break by name).
    sorted_options = sorted(options, key=lambda x: (-float(x["return"]), x["name"]))
    best = sorted_options[0]
    allocation = {opt["name"]: 0.0 for opt in options}
    allocation[best["name"]] = float(total_resource)
    reason = (
        f"Under a linear return assumption, {best['name']} has the highest unit return "
        f"(return={best['return']}), so allocating all resources to this option maximizes total return."
    )
    confidence = 0.99
    return allocation, reason, confidence


def _mcp_result(result: dict, request_id):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_error(code: int, message: str, request_id=None, structured=None):
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if structured is not None:
        payload["error"]["data"] = {"structuredContent": structured}
    return payload


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            return _text_response(self, 200, _load_html("index.html"), "text/html; charset=utf-8")
        if path == "/privacy":
            return _text_response(self, 200, _load_html("privacy.html"), "text/html; charset=utf-8")
        if path == "/terms":
            return _text_response(self, 200, _load_html("terms.html"), "text/html; charset=utf-8")
        if path == "/support":
            return _text_response(self, 200, _load_html("support.html"), "text/html; charset=utf-8")
        if path == "/health":
            return _json_response(self, 200, {"status": "ok"})
        if path == "/.well-known/openai-apps-challenge":
            challenge = os.getenv("OPENAI_APPS_CHALLENGE", "")
            return _text_response(self, 200, challenge)

        _json_response(self, 404, {"error": "Not Found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/mcp":
            return _json_response(self, 404, {"error": "Not Found"})

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length) if content_length > 0 else b""
            request = json.loads(raw.decode("utf-8"))
        except Exception:
            return _json_response(self, 400, _mcp_error(-32700, "Parse error", None))

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {}) if isinstance(request, dict) else {}

        if method == "tools/list":
            return _json_response(
                self,
                200,
                _mcp_result(
                    {
                        "tools": [
                            {
                                "name": "resource_allocation",
                                "description": "Allocate total resources across options to maximize return",
                                "annotations": {
                                    "readOnlyHint": True,
                                    "destructiveHint": True,
                                    "openWorldHint": False,
                                },
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "total_resource": {"type": "number"},
                                        "options": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {"type": "string"},
                                                    "return": {"type": "number"},
                                                },
                                                "required": ["name", "return"],
                                                "additionalProperties": False,
                                            },
                                        },
                                    },
                                    "required": ["total_resource", "options"],
                                    "additionalProperties": False,
                                },
                                "outputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "allocation": {
                                            "type": "object",
                                            "additionalProperties": {"type": "number"},
                                        },
                                        "reason": {"type": "string"},
                                        "confidence": {"type": "number"},
                                    },
                                    "required": ["allocation", "reason", "confidence"],
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    },
                    request_id,
                ),
            )

        if method == "tools/call":
            if not isinstance(params, dict):
                return _json_response(
                    self,
                    200,
                    _mcp_error(-32602, "Invalid params", request_id, {"reason": "params must be an object"}),
                )

            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name != "resource_allocation":
                return _json_response(self, 200, _mcp_error(-32601, "Tool not found", request_id))

            if not isinstance(arguments, dict):
                return _json_response(
                    self,
                    200,
                    _mcp_error(-32602, "Invalid params", request_id, {"reason": "arguments must be an object"}),
                )

            total_resource = arguments.get("total_resource")
            options = arguments.get("options")

            if total_resource is None or options is None:
                return _json_response(
                    self,
                    200,
                    _mcp_error(
                        -32602,
                        "Missing required fields",
                        request_id,
                        {"reason": "Missing required field(s): total_resource and/or options"},
                    ),
                )

            if not isinstance(total_resource, (int, float)) or float(total_resource) < 0:
                return _json_response(
                    self,
                    200,
                    _mcp_error(
                        -32602,
                        "Invalid total_resource",
                        request_id,
                        {"reason": "total_resource must be a number greater than or equal to 0"},
                    ),
                )

            option_error = _validate_options(options)
            if option_error:
                return _json_response(
                    self,
                    200,
                    _mcp_error(-32602, "Invalid options", request_id, {"reason": option_error}),
                )

            allocation, reason, confidence = _allocate(float(total_resource), options)
            structured = {
                "allocation": allocation,
                "reason": reason,
                "confidence": confidence,
            }

            return _json_response(
                self,
                200,
                _mcp_result(
                    {
                        "structuredContent": structured,
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(structured, ensure_ascii=False),
                            }
                        ],
                    },
                    request_id,
                ),
            )

        return _json_response(self, 200, _mcp_error(-32601, "Method not found", request_id))

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
    print(f"{APP_NAME} server listening on 0.0.0.0:{port}")
    server.serve_forever()
