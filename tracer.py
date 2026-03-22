"""
Tracing detalhado do loop agêntico.

Cada invocação de ask_* gera um Trace com Spans (chamadas LLM + tool calls).
Persistido no SQLite via db.save_trace() para visualização via /trace e admin panel.
"""

import json
import time
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Span:
    name: str
    started_at: float  # time.monotonic()
    ended_at: float = 0.0
    input_preview: str = ""
    output_preview: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    error: Optional[str] = None

    def duration_ms(self) -> int:
        end = self.ended_at if self.ended_at else time.monotonic()
        return int((end - self.started_at) * 1000)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms(),
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "error": self.error,
        }


@dataclass
class Trace:
    trace_id: str
    bot_name: str
    user_id: int
    started_at: float   # time.monotonic() para calcular duração
    started_dt: str     # ISO datetime para armazenar no banco
    spans: list = field(default_factory=list)
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def total_latency_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def total_input_tokens(self) -> int:
        return sum(s.tokens_in for s in self.spans)

    def total_output_tokens(self) -> int:
        return sum(s.tokens_out for s in self.spans)

    def total_tool_calls(self) -> int:
        return sum(1 for s in self.spans if s.name.startswith("tool:"))

    def total_llm_calls(self) -> int:
        return sum(1 for s in self.spans if s.name.startswith("llm:"))


def start_trace(bot_name: str, user_id: int, user_message_preview: str = "") -> Trace:
    """Inicia um novo trace para uma invocação de ask_*."""
    return Trace(
        trace_id=str(uuid.uuid4())[:8],
        bot_name=bot_name,
        user_id=user_id,
        started_at=time.monotonic(),
        started_dt=datetime.now().isoformat(),
        metadata={"user_message": user_message_preview[:200]},
    )


def add_span(trace: Trace, name: str, input_preview: str = "") -> Span:
    """Adiciona e retorna um novo span aberto no trace."""
    span = Span(
        name=name,
        started_at=time.monotonic(),
        input_preview=input_preview[:200],
    )
    trace.spans.append(span)
    return span


def end_span(span: Span, output_preview: str = "", tokens_in: int = 0,
             tokens_out: int = 0, error: Optional[str] = None) -> None:
    """Finaliza um span com resultado."""
    span.ended_at = time.monotonic()
    span.output_preview = output_preview[:200]
    span.tokens_in = tokens_in
    span.tokens_out = tokens_out
    span.error = error


def end_trace(trace: Trace, db) -> None:
    """Persiste o trace no SQLite. Silencia erros para não quebrar o fluxo principal."""
    try:
        latency_ms = trace.total_latency_ms()
        spans_json = json.dumps([s.to_dict() for s in trace.spans], ensure_ascii=False)
        meta_json = json.dumps(trace.metadata, ensure_ascii=False)

        db.save_trace(
            trace_id=trace.trace_id,
            bot_name=trace.bot_name,
            user_id=trace.user_id,
            started_at=trace.started_dt,
            total_spans=len(trace.spans),
            total_tool_calls=trace.total_tool_calls(),
            total_llm_calls=trace.total_llm_calls(),
            total_input_tokens=trace.total_input_tokens(),
            total_output_tokens=trace.total_output_tokens(),
            total_latency_ms=latency_ms,
            error=trace.error,
            spans=spans_json,
            metadata=meta_json,
        )
    except Exception as e:
        logger.warning(f"[tracer] Falha ao salvar trace: {e}")


def format_trace_message(trace_dict: dict) -> str:
    """Formata um trace para exibição no Telegram (Markdown v1)."""
    tid = trace_dict.get("id", "?")
    latency = trace_dict.get("total_latency_ms", 0)
    tools = trace_dict.get("total_tool_calls", 0)
    tok_in = trace_dict.get("total_input_tokens", 0)
    tok_out = trace_dict.get("total_output_tokens", 0)
    error = trace_dict.get("error") or ""
    started = (trace_dict.get("started_at") or "")[:19].replace("T", " ")

    lines = [
        f"🔍 *Trace {tid}* | {latency / 1000:.1f}s | {tools} tool(s) | {tok_in + tok_out:,} tokens",
        f"🕐 {started}",
    ]
    if error:
        lines.append(f"❌ `{error[:120]}`")
    lines.append("")

    spans = []
    try:
        spans = json.loads(trace_dict.get("spans", "[]"))
    except Exception:
        pass

    for i, s in enumerate(spans):
        prefix = "└─" if i == len(spans) - 1 else "├─"
        dur = s.get("duration_ms", 0)
        name = s.get("name", "?")
        t_in = s.get("tokens_in", 0)
        t_out = s.get("tokens_out", 0)
        preview = (s.get("output_preview") or "")[:60].replace("\n", " ")
        s_error = s.get("error")

        if name.startswith("llm:"):
            lines.append(f"`{prefix} {name}` ({dur}ms) → {t_in} in, {t_out} out")
        else:
            detail = f' "{preview}"' if preview else ""
            err_tag = " ❌" if s_error else ""
            lines.append(f"`{prefix} {name}` ({dur}ms){err_tag}{detail}")

    return "\n".join(lines)
