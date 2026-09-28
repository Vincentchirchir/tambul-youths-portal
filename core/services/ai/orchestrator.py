import json
import logging
import time
import uuid
from dataclasses import dataclass

from core.models import AIAuditLog
from core.services.ai_tools import AI_TOOL_REGISTRY, invoke_ai_tool
from core.services.ai_tools.constants import (
    AUTHORIZATION_DENIED,
    INVALID_ARGUMENT,
    SENSITIVITY_LOW,
)

from .audit import combine_token_usage, extract_token_usage, record_ai_audit_log
from .config import get_ai_runtime_config
from .errors import (
    AI_INVALID_MODEL_RESPONSE,
    AI_TOOL_LIMIT_EXCEEDED,
    AIServiceError,
)
from .intents import route_ai_message
from .prompts import build_tambul_assistant_instructions
from .provider import create_openai_response, get_openai_client
from .tool_policy import available_ai_tool_definitions, available_ai_tool_names
from .tool_schemas import ai_tool_definitions_to_openai_tools

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIChatResult:
    request_id: str
    message: str
    provider_response_id: str
    tool_calls_executed: int


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


def _get(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_id(response):
    return _get(response, "id", "") or ""


def _response_output(response):
    return _get(response, "output", []) or []


def _extract_output_text(response):
    text = _get(response, "output_text", "")
    if text:
        return text.strip()

    text_parts = []
    for output_item in _response_output(response):
        if _get(output_item, "type") != "message":
            continue
        for content_item in _get(output_item, "content", []) or []:
            if _get(content_item, "type") == "output_text":
                text_value = _get(content_item, "text", "")
                if text_value:
                    text_parts.append(text_value)
    return "\n".join(text_parts).strip()


def _function_calls(response):
    calls = []
    for output_item in _response_output(response):
        if _get(output_item, "type") != "function_call":
            continue
        calls.append(
            ToolCall(
                call_id=_get(output_item, "call_id", "") or "",
                name=_get(output_item, "name", "") or "",
                arguments=_get(output_item, "arguments", "{}") or "{}",
            )
        )
    return calls


def _create_response(client, config, input_items, tools, instructions, tool_choice=None):
    kwargs = dict(
        model=config.model,
        instructions=instructions,
        input=input_items,
        tools=tools,
        max_output_tokens=config.max_output_tokens,
        store=False,
        parallel_tool_calls=False,
    )
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    return create_openai_response(client, **kwargs)


def _parse_tool_arguments(tool_call):
    if not tool_call.call_id or not tool_call.name:
        raise AIServiceError(
            AI_INVALID_MODEL_RESPONSE,
            "Tambul Assistant is temporarily unavailable.",
            status=502,
        )
    try:
        parsed = json.loads(tool_call.arguments or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_error_result(tool_name, code, message):
    return {
        "ok": False,
        "tool": tool_name,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _provider_tool_output(tool_result):
    output = {
        "ok": bool(tool_result.get("ok")),
        "tool": tool_result.get("tool", ""),
    }
    if tool_result.get("ok"):
        output["data"] = tool_result.get("data", {})
    else:
        output["error"] = tool_result.get("error", {})
    return output


def _record_tool_result(
    *,
    request_id,
    user,
    tool_name,
    tool_result,
    provider_response_id,
):
    meta = tool_result.get("meta", {})
    audit_meta = meta.get("audit", {})
    error = tool_result.get("error", {})
    record_ai_audit_log(
        request_id=request_id,
        user=user,
        event_type=AIAuditLog.EVENT_TOOL,
        tool_name=tool_name,
        sensitivity=meta.get("sensitivity", SENSITIVITY_LOW),
        authorization_result=audit_meta.get("authorization_result", ""),
        success=bool(tool_result.get("ok")),
        error_category=audit_meta.get("error_category") or error.get("code", ""),
        provider_response_id=provider_response_id,
    )


def _record_manual_tool_failure(
    *,
    request_id,
    user,
    tool_name,
    sensitivity,
    error_category,
    provider_response_id,
):
    record_ai_audit_log(
        request_id=request_id,
        user=user,
        event_type=AIAuditLog.EVENT_TOOL,
        tool_name=tool_name,
        sensitivity=sensitivity,
        authorization_result=AUTHORIZATION_DENIED,
        success=False,
        error_category=error_category,
        provider_response_id=provider_response_id,
    )


def _record_request_result(
    *,
    request_id,
    user,
    success,
    provider_response_id="",
    token_usage=None,
    duration_ms=None,
    error_category="",
):
    record_ai_audit_log(
        request_id=request_id,
        user=user,
        event_type=AIAuditLog.EVENT_REQUEST,
        success=success,
        error_category=error_category,
        provider_response_id=provider_response_id,
        token_usage=token_usage,
        duration_ms=duration_ms,
    )


def _record_routing_decision(user, routing):
    logger.info(
        "AI request routed",
        extra={
            "user_role": getattr(user, "role", ""),
            "ai_intent": routing.intent,
            "required_tool": routing.required_tool,
            "routing_denied": routing.denied,
            "routing_reason": routing.reason,
            "resolved_argument_names": sorted(routing.resolved_arguments.keys()),
        },
    )


def _tool_choice_for_iteration(routing, tool_calls_executed):
    if not routing.required_tool:
        return None
    if tool_calls_executed == 0:
        return routing.tool_choice
    return "none"


def _apply_routing_arguments(tool_call, arguments, routing):
    if not routing.required_tool:
        return arguments
    if tool_call.name != routing.required_tool:
        return arguments
    if not routing.resolved_arguments:
        return arguments
    return {
        **arguments,
        **routing.resolved_arguments,
    }


def run_tambul_ai_chat(user, message):
    config = get_ai_runtime_config()
    request_id = uuid.uuid4()
    started_at = time.monotonic()
    provider_response_id = ""
    token_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    try:
        tool_definitions = available_ai_tool_definitions(user)
        allowed_tool_names = set(available_ai_tool_names(user))
        routing = route_ai_message(message, allowed_tool_names)
        _record_routing_decision(user, routing)

        if routing.denied:
            _record_request_result(
                request_id=request_id,
                user=user,
                success=True,
                token_usage=token_usage,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return AIChatResult(
                request_id=str(request_id),
                message=routing.denial_message,
                provider_response_id="",
                tool_calls_executed=0,
            )

        client = get_openai_client(config)
        openai_tools = ai_tool_definitions_to_openai_tools(tool_definitions)
        input_items = [{"role": "user", "content": message}]
        instructions = build_tambul_assistant_instructions(routing)
        tool_calls_executed = 0

        while True:
            response = _create_response(
                client,
                config,
                input_items,
                openai_tools,
                instructions,
                tool_choice=_tool_choice_for_iteration(routing, tool_calls_executed),
            )
            provider_response_id = _response_id(response) or provider_response_id
            token_usage = combine_token_usage(
                token_usage,
                extract_token_usage(response),
            )
            function_calls = _function_calls(response)

            if not function_calls:
                if routing.requires_authoritative_tool and tool_calls_executed == 0:
                    raise AIServiceError(
                        AI_INVALID_MODEL_RESPONSE,
                        "Tambul Assistant could not retrieve the requested Tambul record.",
                        status=503,
                    )
                answer = _extract_output_text(response)
                if not answer:
                    raise AIServiceError(
                        AI_INVALID_MODEL_RESPONSE,
                        "Tambul Assistant is temporarily unavailable.",
                        status=502,
                    )
                _record_request_result(
                    request_id=request_id,
                    user=user,
                    success=True,
                    provider_response_id=provider_response_id,
                    token_usage=token_usage,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                return AIChatResult(
                    request_id=str(request_id),
                    message=answer,
                    provider_response_id=provider_response_id,
                    tool_calls_executed=tool_calls_executed,
                )

            if len(function_calls) > 1:
                raise AIServiceError(
                    AI_INVALID_MODEL_RESPONSE,
                    "Tambul Assistant is temporarily unavailable.",
                    status=502,
                )
            if tool_calls_executed >= config.max_tool_calls:
                raise AIServiceError(
                    AI_TOOL_LIMIT_EXCEEDED,
                    "Tambul Assistant used too many data requests.",
                    status=429,
                )

            tool_call = function_calls[0]
            if (
                routing.required_tool
                and tool_calls_executed == 0
                and tool_call.name != routing.required_tool
            ):
                raise AIServiceError(
                    AI_INVALID_MODEL_RESPONSE,
                    "Tambul Assistant could not retrieve the requested Tambul record.",
                    status=503,
                )

            arguments = _parse_tool_arguments(tool_call)
            if arguments is None:
                tool_result = _tool_error_result(
                    tool_call.name,
                    INVALID_ARGUMENT,
                    "Malformed tool arguments.",
                )
                _record_manual_tool_failure(
                    request_id=request_id,
                    user=user,
                    tool_name=tool_call.name,
                    sensitivity=SENSITIVITY_LOW,
                    error_category=INVALID_ARGUMENT,
                    provider_response_id=provider_response_id,
                )
            elif (
                tool_call.name not in allowed_tool_names
                and tool_call.name in AI_TOOL_REGISTRY
            ):
                arguments = _apply_routing_arguments(tool_call, arguments, routing)
                tool_result = invoke_ai_tool(tool_call.name, user, arguments)
                _record_tool_result(
                    request_id=request_id,
                    user=user,
                    tool_name=tool_call.name,
                    tool_result=tool_result,
                    provider_response_id=provider_response_id,
                )
            else:
                arguments = _apply_routing_arguments(tool_call, arguments, routing)
                tool_result = invoke_ai_tool(tool_call.name, user, arguments)
                _record_tool_result(
                    request_id=request_id,
                    user=user,
                    tool_name=tool_call.name,
                    tool_result=tool_result,
                    provider_response_id=provider_response_id,
                )

            tool_calls_executed += 1
            input_items.extend(_response_output(response))
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call.call_id,
                    "output": json.dumps(_provider_tool_output(tool_result)),
                }
            )
    except AIServiceError as exc:
        _record_request_result(
            request_id=request_id,
            user=user,
            success=False,
            provider_response_id=provider_response_id,
            token_usage=token_usage,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_category=exc.code,
        )
        raise
