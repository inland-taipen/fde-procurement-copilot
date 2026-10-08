from __future__ import annotations

from src.agents import run_single_agent, run_staged
from src.contracts import Architecture, ProcurementDecision, RunTelemetry
from src.telemetry import RunTelemetryCounter


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter: route to the requested architecture and attach telemetry."""
    counter = RunTelemetryCounter()
    if architecture == "staged":
        decision = run_staged(request_id, counter)
    else:
        decision = run_single_agent(request_id, counter)
    decision.telemetry = RunTelemetry(
        llm_calls=counter.llm_calls,
        tool_calls=counter.tool_calls,
        tool_names=counter.tool_names,
    )
    return decision
