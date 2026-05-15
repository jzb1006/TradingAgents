"""Small HTTP server that exposes TradingAgents through a browser UI."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import threading
import traceback
import uuid
import webbrowser
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.rating import parse_rating
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.graph.checkpointer import (
    checkpoint_step,
    clear_checkpoint,
    get_checkpointer,
    thread_id,
)
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from tradingagents.dataflows.interface import VENDOR_LIST
from tradingagents.dataflows.utils import safe_ticker_component

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
ANALYST_ORDER = ("market", "social", "news", "fundamentals")
HISTORY_LIMIT = 100
MAX_JOB_EVENTS = 500
PROVIDER_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORTS = {
    "market": ("market_report", "Market and technical report"),
    "social": ("sentiment_report", "Sentiment report"),
    "news": ("news_report", "News and macro report"),
    "fundamentals": ("fundamentals_report", "Company fundamentals report"),
}
FIXED_AGENT_GROUPS = (
    ("Bull Researcher", "Bear Researcher", "Research Manager"),
    ("Trader",),
    ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"),
    ("Portfolio Manager",),
)
ALLOWED_LANGUAGES = {
    "English",
    "Chinese",
    "Japanese",
    "Korean",
    "Hindi",
    "Spanish",
    "Portuguese",
    "French",
    "German",
    "Arabic",
    "Russian",
}


@dataclass
class AnalysisJob:
    id: str
    request: dict[str, Any]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    decision: str | None = None
    signal: str | None = None
    final_state: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    agent_status: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    error_trace: str | None = None

    def mark(self, status: str) -> None:
        self.status = status
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def add_event(
        self,
        event_type: str,
        title: str,
        content: Any = "",
        agent: str | None = None,
    ) -> None:
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        event = {
            "id": len(self.events) + 1,
            "time": datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            "title": title,
            "content": content.strip(),
        }
        if agent:
            event["agent"] = agent
        self.events.append(event)
        if len(self.events) > MAX_JOB_EVENTS:
            self.events = self.events[-MAX_JOB_EVENTS:]
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": self.request,
            "signal": self.signal,
            "decision": self.decision,
            "final_state": self.final_state,
            "events": list(self.events),
            "agent_status": dict(self.agent_status),
            "error": self.error,
            "error_trace": self.error_trace,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._condition = threading.Condition(threading.Lock())
        self._version = 0

    def create(self, payload: dict[str, Any]) -> AnalysisJob:
        job = AnalysisJob(id=uuid.uuid4().hex[:12], request=payload)
        job.agent_status = _initial_agent_status(payload["analysts"])
        job.add_event("system", "Analysis queued", f"{payload['ticker']} · {payload['date']}")
        with self._condition:
            self._jobs[job.id] = job
            self._version += 1
            self._condition.notify_all()
        return job

    def get(self, job_id: str) -> AnalysisJob | None:
        with self._condition:
            return self._jobs.get(job_id)

    def update(self, job: AnalysisJob) -> None:
        job.updated_at = datetime.now().isoformat(timespec="seconds")
        with self._condition:
            self._jobs[job.id] = job
            self._version += 1
            self._condition.notify_all()

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._condition:
            job = self._jobs.get(job_id)
            return job.to_dict() if job is not None else None

    def events_after(self, job_id: str, event_id: int) -> list[dict[str, Any]] | None:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return [
                event
                for event in job.events
                if int(event.get("id", 0)) > event_id
            ]

    def changes_after(
        self,
        job_id: str,
        event_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], int] | None:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            events = [
                event
                for event in job.events
                if int(event.get("id", 0)) > event_id
            ]
            return events, job.to_dict(), self._version

    def wait_for_change(self, version: int, timeout: float = 15.0) -> int:
        with self._condition:
            self._condition.wait_for(lambda: self._version != version, timeout=timeout)
            return self._version


class WebAppState:
    def __init__(self) -> None:
        self.jobs = JobStore()


def _provider_has_credentials(provider: str) -> bool:
    if provider == "ollama":
        return True

    env_var = PROVIDER_API_KEYS.get(provider)
    if not env_var:
        return False
    return bool(os.getenv(env_var))


def _default_provider() -> str:
    configured = DEFAULT_CONFIG["llm_provider"]
    if _provider_has_credentials(configured):
        return configured

    for provider in (
        "deepseek",
        "openai",
        "anthropic",
        "google",
        "qwen",
        "glm",
        "xai",
        "openrouter",
        "azure",
    ):
        if _provider_has_credentials(provider):
            return provider

    return configured


def _default_model(provider: str, mode: str, fallback: str) -> str:
    options = MODEL_OPTIONS.get(provider, {}).get(mode, [])
    if options:
        return options[0][1]
    return fallback


def _response_schema() -> dict[str, Any]:
    provider_models = {
        provider: {
            mode: [{"label": label, "value": value} for label, value in options]
            for mode, options in modes.items()
        }
        for provider, modes in MODEL_OPTIONS.items()
    }
    provider_models["azure"] = {"quick": [], "deep": []}
    provider_models["openrouter"] = {"quick": [], "deep": []}
    default_provider = _default_provider()
    quick_model = _default_model(default_provider, "quick", DEFAULT_CONFIG["quick_think_llm"])
    deep_model = _default_model(default_provider, "deep", DEFAULT_CONFIG["deep_think_llm"])

    return {
        "analysts": list(ANALYST_ORDER),
        "providers": sorted(provider_models.keys()),
        "provider_models": provider_models,
        "data_vendors": VENDOR_LIST,
        "languages": sorted(ALLOWED_LANGUAGES),
        "provider_key_status": {
            provider: _provider_has_credentials(provider)
            for provider in sorted(provider_models.keys())
        },
        "defaults": {
            "ticker": "SPY",
            "date": date.today().isoformat(),
            "provider": default_provider,
            "quick_model": quick_model,
            "deep_model": deep_model,
            "data_vendor": DEFAULT_CONFIG["data_vendors"]["core_stock_apis"],
            "language": DEFAULT_CONFIG["output_language"],
            "research_depth": DEFAULT_CONFIG["max_debate_rounds"],
            "checkpoint": DEFAULT_CONFIG["checkpoint_enabled"],
        },
    }


def _parse_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _history_root() -> Path:
    return Path(DEFAULT_CONFIG["results_dir"]).expanduser()


def _history_id(path: Path) -> str:
    root = _history_root().resolve()
    relative = path.resolve().relative_to(root)
    return relative.as_posix()


def _history_path(history_id: str) -> Path:
    root = _history_root().resolve()
    path = (root / unquote(history_id)).resolve()
    path.relative_to(root)
    if path.name.startswith("full_states_log_") and path.suffix == ".json":
        return path
    raise ValueError("invalid history id")


def _summarize_history(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        history_id = _history_id(path)
        ticker = str(data.get("company_of_interest") or path.parents[1].name)
        trade_date = str(data.get("trade_date") or path.stem.removeprefix("full_states_log_"))
        decision = str(data.get("final_trade_decision") or "")
        return {
            "id": history_id,
            "ticker": ticker,
            "date": trade_date,
            "signal": parse_rating(decision) if decision else "",
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "path": str(path),
        }
    except Exception as exc:
        logger.warning("Skipping unreadable history file %s: %s", path, exc)
        return None


def _list_history(limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    root = _history_root()
    if not root.exists():
        return []

    files = sorted(
        root.glob("*/TradingAgentsStrategy_logs/full_states_log_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    items = []
    for path in files[:limit]:
        summary = _summarize_history(path)
        if summary is not None:
            items.append(summary)
    return items


def _load_history(history_id: str) -> dict[str, Any]:
    path = _history_path(history_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "id": _history_id(path),
        "path": str(path),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "final_state": data,
    }


def _parse_event_id(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _sse_payload(event_name: str, payload: dict[str, Any], event_id: int | None = None) -> bytes:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    data = json.dumps(payload, ensure_ascii=False, default=str)
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _initial_agent_status(analysts: list[str]) -> dict[str, str]:
    status = {
        ANALYST_AGENT_NAMES[analyst]: "pending"
        for analyst in ANALYST_ORDER
        if analyst in analysts
    }
    for group in FIXED_AGENT_GROUPS:
        for agent in group:
            status[agent] = "pending"
    return status


def _set_agent_status(
    app_state: WebAppState,
    job: AnalysisJob,
    agent: str,
    status: str,
) -> None:
    if agent not in job.agent_status or job.agent_status[agent] == status:
        return
    job.agent_status[agent] = status
    job.add_event("status", f"{agent} {status.replace('_', ' ')}", agent=agent)
    app_state.jobs.update(job)


def _add_job_event(
    app_state: WebAppState,
    job: AnalysisJob,
    event_type: str,
    title: str,
    content: Any = "",
    agent: str | None = None,
) -> None:
    job.add_event(event_type, title, content, agent)
    app_state.jobs.update(job)


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text.strip()
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return " ".join(part.strip() for part in parts if part.strip())
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value).strip()


def _classify_message(message: Any) -> tuple[str, str]:
    content = _content_text(getattr(message, "content", None))
    message_type = message.__class__.__name__
    if message_type == "HumanMessage":
        return ("control" if content == "Continue" else "user", content)
    if message_type == "ToolMessage":
        return "data", content
    if message_type == "AIMessage":
        return "message", content
    return "system", content


def _tool_call_parts(tool_call: Any) -> tuple[str, Any]:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "Tool"), tool_call.get("args", {})
    return str(getattr(tool_call, "name", "Tool")), getattr(tool_call, "args", {})


def _emit_if_changed(
    app_state: WebAppState,
    job: AnalysisJob,
    tracker: dict[str, Any],
    key: str,
    event_type: str,
    title: str,
    content: Any,
    agent: str | None = None,
) -> bool:
    text = _content_text(content)
    if not text or tracker["last_values"].get(key) == text:
        return False
    tracker["last_values"][key] = text
    _add_job_event(app_state, job, event_type, title, text, agent)
    return True


def _process_messages(
    app_state: WebAppState,
    job: AnalysisJob,
    tracker: dict[str, Any],
    chunk: dict[str, Any],
) -> None:
    for message in chunk.get("messages", []):
        msg_id = getattr(message, "id", None)
        event_type, content = _classify_message(message)
        tool_calls = getattr(message, "tool_calls", None) or []
        fallback_id = (
            message.__class__.__name__,
            content,
            json.dumps(tool_calls, ensure_ascii=False, default=str),
        )
        dedupe_key = msg_id or fallback_id
        if dedupe_key in tracker["seen_messages"]:
            continue
        tracker["seen_messages"].add(dedupe_key)

        if content:
            _add_job_event(app_state, job, event_type, event_type.title(), content)

        for tool_call in tool_calls:
            name, args = _tool_call_parts(tool_call)
            _add_job_event(app_state, job, "tool", name, args)


def _process_analyst_reports(
    app_state: WebAppState,
    job: AnalysisJob,
    tracker: dict[str, Any],
    chunk: dict[str, Any],
) -> None:
    active_found = False
    for analyst in ANALYST_ORDER:
        if analyst not in job.request["analysts"]:
            continue
        agent = ANALYST_AGENT_NAMES[analyst]
        report_key, subtitle = ANALYST_REPORTS[analyst]
        if _emit_if_changed(
            app_state,
            job,
            tracker,
            report_key,
            "report",
            f"{agent}: {subtitle}",
            chunk.get(report_key),
            agent,
        ):
            tracker["completed_reports"].add(report_key)

        if report_key in tracker["completed_reports"]:
            _set_agent_status(app_state, job, agent, "completed")
        elif not active_found:
            _set_agent_status(app_state, job, agent, "in_progress")
            active_found = True
        else:
            _set_agent_status(app_state, job, agent, "pending")

    if not active_found and job.request["analysts"]:
        _set_agent_status(app_state, job, "Bull Researcher", "in_progress")


def _process_debates(
    app_state: WebAppState,
    job: AnalysisJob,
    tracker: dict[str, Any],
    chunk: dict[str, Any],
) -> None:
    debate = chunk.get("investment_debate_state") or {}
    if debate:
        if _emit_if_changed(
            app_state,
            job,
            tracker,
            "investment_debate_state.bull_history",
            "debate",
            "Bull Researcher",
            debate.get("bull_history"),
            "Bull Researcher",
        ):
            _set_agent_status(app_state, job, "Bull Researcher", "in_progress")
        if _emit_if_changed(
            app_state,
            job,
            tracker,
            "investment_debate_state.bear_history",
            "debate",
            "Bear Researcher",
            debate.get("bear_history"),
            "Bear Researcher",
        ):
            _set_agent_status(app_state, job, "Bull Researcher", "completed")
            _set_agent_status(app_state, job, "Bear Researcher", "in_progress")
        if _emit_if_changed(
            app_state,
            job,
            tracker,
            "investment_debate_state.judge_decision",
            "decision",
            "Research Manager",
            debate.get("judge_decision"),
            "Research Manager",
        ):
            _set_agent_status(app_state, job, "Bear Researcher", "completed")
            _set_agent_status(app_state, job, "Research Manager", "completed")
            _set_agent_status(app_state, job, "Trader", "in_progress")

    if _emit_if_changed(
        app_state,
        job,
        tracker,
        "trader_investment_plan",
        "report",
        "Trader",
        chunk.get("trader_investment_plan"),
        "Trader",
    ):
        _set_agent_status(app_state, job, "Trader", "completed")
        _set_agent_status(app_state, job, "Aggressive Analyst", "in_progress")

    risk = chunk.get("risk_debate_state") or {}
    if not risk:
        return

    if _emit_if_changed(
        app_state,
        job,
        tracker,
        "risk_debate_state.aggressive_history",
        "debate",
        "Aggressive Risk Analyst",
        risk.get("aggressive_history"),
        "Aggressive Analyst",
    ):
        _set_agent_status(app_state, job, "Aggressive Analyst", "in_progress")
    if _emit_if_changed(
        app_state,
        job,
        tracker,
        "risk_debate_state.conservative_history",
        "debate",
        "Conservative Risk Analyst",
        risk.get("conservative_history"),
        "Conservative Analyst",
    ):
        _set_agent_status(app_state, job, "Aggressive Analyst", "completed")
        _set_agent_status(app_state, job, "Conservative Analyst", "in_progress")
    if _emit_if_changed(
        app_state,
        job,
        tracker,
        "risk_debate_state.neutral_history",
        "debate",
        "Neutral Risk Analyst",
        risk.get("neutral_history"),
        "Neutral Analyst",
    ):
        _set_agent_status(app_state, job, "Conservative Analyst", "completed")
        _set_agent_status(app_state, job, "Neutral Analyst", "in_progress")
    if _emit_if_changed(
        app_state,
        job,
        tracker,
        "risk_debate_state.judge_decision",
        "decision",
        "Portfolio Manager",
        risk.get("judge_decision"),
        "Portfolio Manager",
    ):
        _set_agent_status(app_state, job, "Aggressive Analyst", "completed")
        _set_agent_status(app_state, job, "Conservative Analyst", "completed")
        _set_agent_status(app_state, job, "Neutral Analyst", "completed")
        _set_agent_status(app_state, job, "Portfolio Manager", "completed")


def _process_stream_chunk(
    app_state: WebAppState,
    job: AnalysisJob,
    tracker: dict[str, Any],
    chunk: dict[str, Any],
) -> None:
    _process_messages(app_state, job, tracker, chunk)
    _process_analyst_reports(app_state, job, tracker, chunk)
    _process_debates(app_state, job, tracker, chunk)


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ticker = safe_ticker_component(str(payload.get("ticker", "")).strip().upper())
    analysis_date = str(payload.get("date", "")).strip()
    datetime.strptime(analysis_date, "%Y-%m-%d")

    if datetime.strptime(analysis_date, "%Y-%m-%d").date() > date.today():
        raise ValueError("analysis date cannot be in the future")

    analysts = payload.get("analysts") or list(ANALYST_ORDER)
    if not isinstance(analysts, list):
        raise ValueError("analysts must be a list")
    selected_analysts = [a for a in ANALYST_ORDER if a in analysts]
    if not selected_analysts:
        raise ValueError("select at least one analyst")

    provider = str(payload.get("provider", DEFAULT_CONFIG["llm_provider"])).strip().lower()
    allowed_providers = set(MODEL_OPTIONS) | {"azure", "openrouter"}
    if provider not in allowed_providers:
        raise ValueError(f"unsupported provider: {provider}")
    if not _provider_has_credentials(provider):
        env_var = PROVIDER_API_KEYS.get(provider)
        if env_var:
            raise ValueError(f"{provider} requires {env_var} in .env or environment")
        raise ValueError(f"{provider} is not configured")

    data_vendor = str(
        payload.get("data_vendor", DEFAULT_CONFIG["data_vendors"]["core_stock_apis"])
    ).strip()
    if data_vendor not in VENDOR_LIST:
        raise ValueError(f"unsupported data vendor: {data_vendor}")

    language = str(payload.get("language", DEFAULT_CONFIG["output_language"])).strip()
    if not language:
        language = DEFAULT_CONFIG["output_language"]

    depth = int(payload.get("research_depth", DEFAULT_CONFIG["max_debate_rounds"]))
    if depth < 1 or depth > 5:
        raise ValueError("research depth must be between 1 and 5")

    quick_model = str(payload.get("quick_model") or DEFAULT_CONFIG["quick_think_llm"]).strip()
    deep_model = str(payload.get("deep_model") or DEFAULT_CONFIG["deep_think_llm"]).strip()
    if not quick_model or not deep_model:
        raise ValueError("quick_model and deep_model are required")

    return {
        "ticker": ticker,
        "date": analysis_date,
        "analysts": selected_analysts,
        "provider": provider,
        "quick_model": quick_model,
        "deep_model": deep_model,
        "data_vendor": data_vendor,
        "language": language,
        "research_depth": depth,
        "checkpoint": bool(payload.get("checkpoint", False)),
        "backend_url": str(payload.get("backend_url") or "").strip() or None,
    }


def _build_config(request: dict[str, Any]) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    config["llm_provider"] = request["provider"]
    config["quick_think_llm"] = request["quick_model"]
    config["deep_think_llm"] = request["deep_model"]
    config["backend_url"] = request["backend_url"]
    config["max_debate_rounds"] = request["research_depth"]
    config["max_risk_discuss_rounds"] = request["research_depth"]
    config["output_language"] = request["language"]
    config["checkpoint_enabled"] = request["checkpoint"]
    config["data_vendors"] = {
        "core_stock_apis": request["data_vendor"],
        "technical_indicators": request["data_vendor"],
        "fundamental_data": request["data_vendor"],
        "news_data": request["data_vendor"],
    }
    return config


def _stream_analysis(
    app_state: WebAppState,
    job: AnalysisJob,
    graph: TradingAgentsGraph,
) -> tuple[dict[str, Any], str]:
    ticker = job.request["ticker"]
    trade_date = job.request["date"]
    graph.ticker = ticker
    graph._resolve_pending_entries(ticker)

    if graph.config.get("checkpoint_enabled"):
        graph._checkpointer_ctx = get_checkpointer(graph.config["data_cache_dir"], ticker)
        saver = graph._checkpointer_ctx.__enter__()
        graph.graph = graph.workflow.compile(checkpointer=saver)
        step = checkpoint_step(graph.config["data_cache_dir"], ticker, str(trade_date))
        if step is None:
            _add_job_event(app_state, job, "system", "Checkpoint", "Starting fresh run.")
        else:
            _add_job_event(app_state, job, "system", "Checkpoint", f"Resuming from step {step}.")

    try:
        past_context = graph.memory_log.get_past_context(ticker)
        init_agent_state = graph.propagator.create_initial_state(
            ticker,
            trade_date,
            past_context=past_context,
        )
        args = graph.propagator.get_graph_args()
        if graph.config.get("checkpoint_enabled"):
            tid = thread_id(ticker, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        tracker = {
            "seen_messages": set(),
            "last_values": {},
            "completed_reports": set(),
        }
        trace: list[dict[str, Any]] = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            _process_stream_chunk(app_state, job, tracker, chunk)
            trace.append(chunk)

        if not trace:
            raise RuntimeError("analysis stream did not return a final state")

        final_state = trace[-1]
        for agent in list(job.agent_status):
            _set_agent_status(app_state, job, agent, "completed")
        graph.curr_state = final_state
        graph._log_state(trade_date, final_state)
        graph.memory_log.store_decision(
            ticker=ticker,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )
        if graph.config.get("checkpoint_enabled"):
            clear_checkpoint(graph.config["data_cache_dir"], ticker, str(trade_date))

        return final_state, graph.process_signal(final_state["final_trade_decision"])
    finally:
        if graph._checkpointer_ctx is not None:
            graph._checkpointer_ctx.__exit__(None, None, None)
            graph._checkpointer_ctx = None
            graph.graph = graph.workflow.compile()


def _run_analysis(state: WebAppState, job: AnalysisJob) -> None:
    job.mark("running")
    job.add_event(
        "system",
        "Analysis started",
        "Live Agent outputs and tool calls will appear here while the workflow runs.",
    )
    state.jobs.update(job)
    try:
        config = _build_config(job.request)
        graph = TradingAgentsGraph(
            selected_analysts=job.request["analysts"],
            debug=False,
            config=config,
        )
        final_state, signal = _stream_analysis(state, job, graph)
        job.final_state = final_state
        job.signal = signal
        job.decision = final_state.get("final_trade_decision", "")
        job.add_event("system", "Analysis completed", f"Signal: {signal}")
        job.mark("completed")
    except Exception as exc:
        logger.exception("analysis job %s failed", job.id)
        job.error = str(exc)
        job.error_trace = traceback.format_exc(limit=8)
        job.add_event("error", "Analysis failed", str(exc))
        job.mark("failed")
    finally:
        state.jobs.update(job)


def _safe_static_path(path: str) -> Path | None:
    relative = "index.html" if path in ("", "/") else path.lstrip("/")
    candidate = (STATIC_DIR / relative).resolve()
    try:
        candidate.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def create_handler(app_state: WebAppState) -> type[BaseHTTPRequestHandler]:
    class TradingAgentsWebHandler(BaseHTTPRequestHandler):
        server_version = "TradingAgentsWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                self._send_json(_response_schema())
                return

            if parsed.path == "/api/history":
                self._send_json({"items": _list_history()})
                return

            if parsed.path.startswith("/api/history/"):
                history_id = parsed.path.removeprefix("/api/history/")
                try:
                    self._send_json(_load_history(history_id))
                except FileNotFoundError:
                    self._send_json({"error": "history item not found"}, HTTPStatus.NOT_FOUND)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/events"):
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/events").strip("/")
                query = parse_qs(parsed.query)
                after = _parse_event_id(query.get("after", [None])[0])
                last_event_id = _parse_event_id(self.headers.get("Last-Event-ID"))
                self._send_job_events(job_id, max(after, last_event_id))
                return

            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = app_state.jobs.get(job_id)
                if job is None:
                    self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(job.to_dict())
                return

            static_path = _safe_static_path(parsed.path)
            if static_path is None:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_file(static_path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/analyze":
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return

            try:
                payload = _parse_json_body(self)
                request = _validate_payload(payload)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            job = app_state.jobs.create(request)
            worker = threading.Thread(
                target=_run_analysis,
                args=(app_state, job),
                name=f"analysis-{job.id}",
                daemon=True,
            )
            worker.start()
            self._send_json(job.to_dict(), HTTPStatus.ACCEPTED)

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(
            self,
            payload: dict[str, Any],
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            body = path.read_bytes()
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_sse(self, event_name: str, payload: dict[str, Any], event_id: int | None = None) -> None:
            self.wfile.write(_sse_payload(event_name, payload, event_id))
            self.wfile.flush()

        def _send_job_events(self, job_id: str, after: int) -> None:
            snapshot = app_state.jobs.snapshot(job_id)
            if snapshot is None:
                self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            last_sent = after
            terminal_statuses = {"completed", "failed"}

            try:
                self._send_sse("snapshot", snapshot)
                version = 0
                while True:
                    changes = app_state.jobs.changes_after(job_id, last_sent)
                    if changes is None:
                        self._send_sse("error", {"error": "job not found"})
                        return
                    events, snapshot, version = changes

                    for event in events:
                        event_id = int(event.get("id", 0))
                        last_sent = max(last_sent, event_id)
                        self._send_sse("job_event", event, event_id)

                    self._send_sse("job_update", snapshot)
                    if snapshot["status"] in terminal_statuses:
                        self._send_sse(snapshot["status"], snapshot)
                        return

                    version = app_state.jobs.wait_for_change(version)
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return TradingAgentsWebHandler


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    load_dotenv()
    load_dotenv(".env.enterprise", override=False)
    logging.basicConfig(level=os.getenv("TRADINGAGENTS_WEB_LOG_LEVEL", "INFO"))

    app_state = WebAppState()
    server = ThreadingHTTPServer((host, port), create_handler(app_state))
    url = f"http://{host}:{server.server_port}"
    print(f"TradingAgents Web is running at {url}")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TradingAgents Web...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TradingAgents Web UI.")
    parser.add_argument("--host", default=os.getenv("TRADINGAGENTS_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TRADINGAGENTS_WEB_PORT", "8000")))
    parser.add_argument("--open", action="store_true", help="Open the Web UI in the default browser.")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, open_browser=args.open)
