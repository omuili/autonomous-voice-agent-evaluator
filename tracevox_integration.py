from __future__ import annotations

import base64
import logging
import math
import queue
import re
import threading
import time
from typing import Any, Optional

from config import (
    REALTIME_MODEL,
    REALTIME_VOICE,
    TRACEVOX_API_KEY,
    TRACEVOX_BASE_URL,
    TRACEVOX_CAMPAIGN_ID,
    TRACEVOX_ENABLED,
    TRACEVOX_ENVIRONMENT,
)

logger = logging.getLogger("tracevox.integration")

APPLICATION_NAME = "autonomous-voice-agent"
TARGET_SYSTEM = "Pretty Good AI"
TEST_TYPE = "voice_agent_evaluation"

SPEAKER_PATIENT = "patient_simulator"
SPEAKER_TARGET = "target_agent"

ENVELOPE_BUCKET_SECONDS = 0.25   
ENVELOPE_SAMPLE_STRIDE = 2     
SPEECH_RMS_THRESHOLD = 0.015    
QUEUE_MAX = 10_000               

_settings: dict[str, Any] = {
    "enabled": TRACEVOX_ENABLED,
    "api_key": TRACEVOX_API_KEY,
    "base_url": TRACEVOX_BASE_URL,
    "environment": TRACEVOX_ENVIRONMENT,
    "default_campaign_id": TRACEVOX_CAMPAIGN_ID,
}


def configure(**overrides: Any) -> None:
    """Override adapter settings (used by tests)."""
    for key, value in overrides.items():
        if key not in _settings:
            raise KeyError(f"Unknown TraceVox setting: {key}")
        _settings[key] = value


def tracevox_enabled() -> bool:
    return bool(_settings["enabled"]) and bool(_settings["api_key"])


def _default_client_factory():
    from tracevox_runs_client import TracevoxRuns

    return TracevoxRuns(
        api_key=_settings["api_key"],
        base_url=_settings["base_url"],
    )


client_factory = _default_client_factory



_MULAW_BIAS = 0x84


def _mulaw_decode_byte(value: int) -> int:
    value = ~value & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = (((mantissa << 3) + _MULAW_BIAS) << exponent) - _MULAW_BIAS
    return -magnitude if sign else magnitude



_MULAW_NORM = tuple(_mulaw_decode_byte(b) / 32768.0 for b in range(256))


_registry: dict[str, "TracevoxCallRun"] = {}
_registry_lock = threading.Lock()


def get_or_start_run(
    call_sid: str,
    scenario_id: Optional[str] = None,
    *,
    scenario_title: Optional[str] = None,
    campaign_id: Optional[str] = None,
    stream_sid: Optional[str] = None,
) -> "TracevoxCallRun":
    with _registry_lock:
        run = _registry.get(call_sid)
        if run is None:
            run = TracevoxCallRun(
                call_sid=call_sid,
                scenario_id=scenario_id,
                scenario_title=scenario_title,
                campaign_id=campaign_id or _settings["default_campaign_id"] or None,
                stream_sid=stream_sid,
            )
            _registry[call_sid] = run
        return run


def get_run(call_sid: str) -> Optional["TracevoxCallRun"]:
    with _registry_lock:
        return _registry.get(call_sid)


def _unregister(call_sid: str) -> None:
    with _registry_lock:
        _registry.pop(call_sid, None)


def _reset_for_tests() -> None:
    with _registry_lock:
        _registry.clear()



class TracevoxCallRun:
    def __init__(
        self,
        call_sid: str,
        scenario_id: Optional[str] = None,
        scenario_title: Optional[str] = None,
        campaign_id: Optional[str] = None,
        stream_sid: Optional[str] = None,
    ):
        self.call_sid = call_sid
        self.scenario_id = scenario_id
        self.scenario_title = scenario_title
        self.campaign_id = campaign_id
        self.stream_sid = stream_sid
        self.enabled = tracevox_enabled()

        self._t0 = time.monotonic()
        self._completed = False
        self._complete_lock = threading.Lock()
        self._drop_logged = False
        self._queue: Optional[queue.Queue] = None
        self._thread: Optional[threading.Thread] = None

        if self.enabled:
            self._queue = queue.Queue(maxsize=QUEUE_MAX)
            self._thread = threading.Thread(
                target=self._worker,
                name=f"tracevox-run-{call_sid[-8:]}",
                daemon=True,
            )
            self._thread.start()

 
    def mark_media_start(self) -> None:
        self._t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._t0


    def _put(self, op: tuple) -> None:
        if not self.enabled or self._completed or self._queue is None:
            return
        try:
            self._queue.put_nowait(op)
        except queue.Full:
            if not self._drop_logged:
                self._drop_logged = True
                logger.warning(
                    "TraceVox queue full for %s — dropping telemetry (call unaffected).",
                    self.call_sid,
                )
        except Exception as exc:  # absolute isolation
            logger.warning("TraceVox enqueue failed: %r", exc)

    def event(
        self,
        event_type: str,
        attributes: Optional[dict[str, Any]] = None,
        source: str = "app",
        severity: str = "info",
    ) -> None:
        self._put(("event", event_type, dict(attributes or {}), source, severity))

    def metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        dimensions: Optional[dict[str, Any]] = None,
    ) -> None:
        self._put(("metric", name, value, unit, dict(dimensions or {})))

    def transcript(
        self,
        speaker: str,
        text: str,
        start_time: float,
        end_time: Optional[float] = None,
        source: str = "",
        segment_id: Optional[str] = None,
    ) -> None:
        self._put(("transcript", speaker, text, start_time, end_time, source, segment_id))

    def envelope_frame(
        self,
        speaker: str,
        payload_b64: str,
        t: Optional[float] = None,
    ) -> None:

        self._put(("frame", speaker, payload_b64, self.elapsed() if t is None else t))

    def flush_envelope(self) -> None:
        self._put(("flush_env",))

    def finding(
        self,
        category: str,
        title: str,
        description: str = "",
        severity: str = "medium",
        confidence: float = 0.5,
        failure_attribution: str = "inconclusive",
        evidence: Optional[list[dict[str, Any]]] = None,
        expected_behavior: str = "",
        impact: str = "",
    ) -> None:
        self._put((
            "finding",
            {
                "category": category,
                "title": title,
                "description": description,
                "severity": severity,
                "confidence": confidence,
                "failure_attribution": failure_attribution,
                "evidence": evidence or [],
                "expected_behavior": expected_behavior,
                "impact": impact,
            },
        ))

    def complete(
        self,
        status: str = "completed",
        outcome: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._complete_lock:
            if self._completed:
                return
            already_disabled = not self.enabled or self._queue is None
            if not already_disabled:
                try:
                    self._queue.put(("flush_env",), timeout=1.0)
                    self._queue.put(
                        ("complete", status, outcome, dict(metadata or {})),
                        timeout=1.0,
                    )
                    self._queue.put(None, timeout=1.0)
                except Exception as exc:
                    logger.warning("TraceVox completion enqueue failed: %r", exc)
            self._completed = True
        _unregister(self.call_sid)

    def join(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "application": APPLICATION_NAME,
            "test_type": TEST_TYPE,
            "target_system": TARGET_SYSTEM,
            "environment": _settings["environment"],
            "twilio_call_sid": self.call_sid,
            "realtime_model": REALTIME_MODEL,
            "realtime_voice": REALTIME_VOICE,
        }
        if self.scenario_id:
            metadata["scenario_id"] = self.scenario_id
        if self.scenario_title:
            metadata["scenario_title"] = self.scenario_title
        if self.campaign_id:
            metadata["campaign_id"] = self.campaign_id
        if self.stream_sid:
            metadata["twilio_stream_sid"] = self.stream_sid
        return metadata

    def _worker(self) -> None:
        handle = None
        try:
            client = client_factory()
            name = self.scenario_title or self.scenario_id or "voice call"
            handle = client.start_run(
                run_type="voice",
                name=f"{name} · {self.call_sid[-8:]}",
                project="autonomous-voice-agent",
                external_id=self.call_sid,
                campaign_id=self.campaign_id,
                scenario=self.scenario_id,
                environment=_settings["environment"],
                tags=["voice", "twilio", "realtime"],
                metadata=self._run_metadata(),
            )
        except Exception as exc:
            logger.warning("TraceVox run creation failed for %s: %r", self.call_sid, exc)
            handle = None

        buckets: dict[str, list] = {}
        while True:
            op = self._queue.get()
            if op is None:
                break
            if handle is None:
                continue
            try:
                self._dispatch(handle, buckets, op)
            except Exception as exc:
                logger.warning("TraceVox op %s failed: %r", op[0], exc)

        if handle is not None:
            try:
                handle.flush()
            except Exception:
                pass

    def _dispatch(self, handle, buckets: dict[str, list], op: tuple) -> None:
        kind = op[0]
        if kind == "event":
            _, event_type, attributes, source, severity = op
            handle.event(event_type, attributes=attributes, source=source, severity=severity)
        elif kind == "metric":
            _, name, value, unit, dimensions = op
            handle.metric(name, value, unit=unit, dimensions=dimensions)
        elif kind == "transcript":
            _, speaker, text, start_time, end_time, source, segment_id = op
            handle.transcript(
                speaker,
                text,
                start_time=start_time,
                end_time=end_time,
                source=source,
                segment_id=segment_id,
            )
        elif kind == "frame":
            _, speaker, payload_b64, t = op
            self._process_frame(handle, buckets, speaker, payload_b64, t)
        elif kind == "flush_env":
            for speaker in list(buckets):
                self._emit_bucket(handle, speaker, buckets.pop(speaker))
        elif kind == "finding":
            handle.finding(**op[1])
        elif kind == "complete":
            _, status, outcome, metadata = op
            for speaker in list(buckets):
                self._emit_bucket(handle, speaker, buckets.pop(speaker))
            handle.complete(status=status, outcome=outcome, metadata=metadata)

    def _process_frame(
        self,
        handle,
        buckets: dict[str, list],
        speaker: str,
        payload_b64: str,
        t: float,
    ) -> None:
        try:
            raw = base64.b64decode(payload_b64)
        except Exception:
            return
        if not raw:
            return

        table = _MULAW_NORM
        sum_sq = 0.0
        peak = 0.0
        samples = raw[::ENVELOPE_SAMPLE_STRIDE]
        for byte in samples:
            value = table[byte]
            sum_sq += value * value
            magnitude = value if value >= 0 else -value
            if magnitude > peak:
                peak = magnitude

        bucket_index = int(t // ENVELOPE_BUCKET_SECONDS)
        state = buckets.get(speaker)
        if state is not None and state[0] != bucket_index:
            self._emit_bucket(handle, speaker, state)
            state = None
        if state is None:
            state = [bucket_index, 0.0, 0.0, 0] 
            buckets[speaker] = state
        state[1] += sum_sq
        if peak > state[2]:
            state[2] = peak
        state[3] += len(samples)

    def _emit_bucket(self, handle, speaker: str, state: list) -> None:
        bucket_index, sum_sq, peak, count = state
        if count <= 0:
            return
        rms = math.sqrt(sum_sq / count)
        handle.envelope([
            {
                "t": round(bucket_index * ENVELOPE_BUCKET_SECONDS, 2),
                "speaker": speaker,
                "rms": round(rms, 4),
                "peak": round(peak, 4),
                "speech_active": rms >= SPEECH_RMS_THRESHOLD,
            }
        ])

_NEW_PATIENT_RE = re.compile(r"\bnew patient\b", re.IGNORECASE)
_IDENTITY_REJECT_RE = re.compile(
    r"(that(?:'s| is) not (?:me|my)\b"
    r"|\bnot my (?:phone[ -]?)?number\b"
    r"|\bnot my name\b"
    r"|\byou have the wrong (?:person|patient|name|number)\b"
    r"|\bi(?:'m| am) not that person\b"
    r"|\bi(?:'m| am) not an existing patient\b"
    r"|\bi (?:don't|do not) go by\b)",
    re.IGNORECASE,
)
_PHONE_WORD_RE = re.compile(r"\b(phone|number)\b", re.IGNORECASE)
_NAME_WORD_RE = re.compile(r"\bname\b|\bperson\b|\bgo by\b", re.IGNORECASE)
_IDENTITY_RESTATE_RE = re.compile(
    r"\bmy name is\b|\bit(?:'s| is) spelled\b|\bi(?:'m| am) [A-Z]", re.IGNORECASE
)


def detect_progress_signals(text: str) -> list[tuple[str, dict[str, Any]]]:
    signals: list[tuple[str, dict[str, Any]]] = []
    if not text:
        return signals

    if _NEW_PATIENT_RE.search(text):
        signals.append(("patient.declared_new_patient", {}))

    rejection = _IDENTITY_REJECT_RE.search(text)
    if rejection:
        if _PHONE_WORD_RE.search(text):
            identifier_type = "phone"
        elif _NAME_WORD_RE.search(text):
            identifier_type = "name"
        else:
            identifier_type = "identity"
        signals.append(("identity.identifier_rejected", {"identifier_type": identifier_type}))
        if _IDENTITY_RESTATE_RE.search(text):
            signals.append(("identity.correction", {"identifier_type": identifier_type}))

    return signals


_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def evaluation_outcome(evaluation: Any) -> str:
    attribution = evaluation.failure_attribution
    if attribution == "none" and evaluation.scenario_goal_reached:
        return "success"
    if attribution == "target_agent":
        return "failure"
    if attribution == "inconclusive":
        return "inconclusive"
    return "partial"


def evaluation_completion_metadata(evaluation: Any) -> dict[str, Any]:
    return {
        "scenario_goal_reached": evaluation.scenario_goal_reached,
        "primary_failure_attribution": evaluation.failure_attribution,
        "target_agent_bug_count": len(evaluation.bugs),
        "simulator_issue_count": len(evaluation.simulator_issues),
        "infrastructure_issue_count": len(evaluation.infrastructure_issues),
        "coherence_score": evaluation.coherence_score,
        "turn_taking_score": evaluation.turn_taking_score,
        "conversation_coherent": evaluation.conversation_coherent,
    }


def send_evaluation_to_tracevox(
    run: TracevoxCallRun,
    evaluation: Any,
    scenario_id: str,
) -> None:
    dimensions = {"scenario_id": scenario_id}
    run.metric("coherence_score", float(evaluation.coherence_score), dimensions=dimensions)
    run.metric("turn_taking_score", float(evaluation.turn_taking_score), dimensions=dimensions)
    run.metric(
        "scenario_goal_reached",
        1.0 if evaluation.scenario_goal_reached else 0.0,
        dimensions=dimensions,
    )

    def _announce(title: str, severity: str, attribution: str, category: str) -> None:
        run.event(
            "assurance.finding.created",
            {
                "title": title,
                "severity": severity,
                "failure_attribution": attribution,
                "category": category,
                "scenario_id": scenario_id,
            },
            source="evaluator",
        )

    for bug in evaluation.bugs:
        severity = _SEVERITY_MAP.get(str(bug.severity).lower(), "medium")
        run.finding(
            category=bug.category or "target_agent_bug",
            title=bug.title,
            description=bug.evidence,
            severity=severity,
            confidence=bug.confidence,
            failure_attribution="target_agent",
            evidence=[{"type": "transcript_timestamp", "ref": bug.timestamp, "note": bug.evidence}],
            expected_behavior=bug.expected_behavior,
            impact=bug.why_it_matters,
        )
        _announce(bug.title, severity, "target_agent", bug.category or "target_agent_bug")

    for issue in evaluation.simulator_issues:
        run.finding(
            category="simulator_behavior",
            title=issue.title,
            description=issue.evidence,
            severity="medium",
            confidence=issue.confidence,
            failure_attribution="simulator",
            evidence=[{"type": "transcript_timestamp", "ref": issue.timestamp, "note": issue.evidence}],
            impact=issue.impact,
        )
        _announce(issue.title, "medium", "simulator", "simulator_behavior")

    for issue in evaluation.infrastructure_issues:
        run.finding(
            category="infrastructure",
            title=issue.title,
            description=issue.evidence,
            severity="medium",
            confidence=issue.confidence,
            failure_attribution="infrastructure",
            evidence=[{"type": "transcript_timestamp", "ref": issue.timestamp, "note": issue.evidence}],
            impact=issue.impact,
        )
        _announce(issue.title, "medium", "infrastructure", "infrastructure")


def send_diarized_transcript(
    run: TracevoxCallRun,
    transcript: dict[str, Any],
    patient_speaker: str,
    target_speaker: str,
) -> None:
    for index, segment in enumerate(transcript.get("segments", [])):
        raw_speaker = str(segment.get("speaker", "Unknown"))
        if raw_speaker == patient_speaker:
            label = "Patient Simulator"
        elif raw_speaker == target_speaker:
            label = "Pretty Good AI Agent"
        else:
            label = f"Speaker {raw_speaker}"

        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        run.transcript(
            speaker=label,
            text=text,
            start_time=start,
            end_time=end,
            source="post_call_diarization",
            segment_id=f"seg_{index:04d}",
        )
