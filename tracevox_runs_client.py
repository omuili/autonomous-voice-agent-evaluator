from __future__ import annotations

import atexit
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("tracevox.client")

DEFAULT_BASE_URL = "https://api.tracevox.ai"
DEFAULT_FLUSH_INTERVAL = 1.0     
DEFAULT_MAX_BATCH = 50


DEFAULT_CREATE_TIMEOUT = 12.0            
DEFAULT_CREATE_ATTEMPTS = 3              
DEFAULT_CREATE_BACKOFFS = (1.0, 3.0)    


DEFAULT_QUEUE_LIMITS = {
    "events": 1000,
    "metrics": 1000,
    "segments": 500,
    "samples": 2400,    
    "findings": 100,
}

_STATE_ACTIVE = "active"    
_STATE_PENDING = "pending"   
_STATE_OFFLINE = "offline"  


class TracevoxRuns:

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        fail_silently: bool = True,
        create_timeout: float = DEFAULT_CREATE_TIMEOUT,
        create_attempts: int = DEFAULT_CREATE_ATTEMPTS,
        create_backoffs: Tuple[float, ...] = DEFAULT_CREATE_BACKOFFS,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.fail_silently = fail_silently
        self.create_timeout = create_timeout
        self.create_attempts = max(1, int(create_attempts))
        self.create_backoffs = tuple(create_backoffs)
        self._session = requests.Session()
        self._session.headers.update({
            "X-Tracevox-Key": api_key,
            "Content-Type": "application/json",
        })

  
    def _request(self, method: str, path: str, json_body: Optional[dict] = None,
                 timeout: Optional[float] = None) -> Optional[dict]:
        try:
            resp = self._session.request(
                method, f"{self.base_url}{path}", json=json_body,
                timeout=timeout if timeout is not None else self.timeout,
            )
            if resp.status_code >= 400:
                message = f"Tracevox API {resp.status_code}: {resp.text[:300]}"
                if self.fail_silently:
                    logger.warning(message)
                    return None
                raise RuntimeError(message)
            return resp.json()
        except requests.RequestException as e:
            if self.fail_silently:
                logger.warning(f"Tracevox request failed ({path}): {e}")
                return None
            raise

    def _create_run_once(self, body: dict) -> Tuple[Optional[dict], bool, bool]:
        try:
            resp = self._session.request(
                "POST", f"{self.base_url}/v1/runs", json=body,
                timeout=self.create_timeout,
            )
        except requests.Timeout as e:
            if not self.fail_silently:
                raise
            logger.warning(f"Tracevox request failed (/v1/runs): {e}")
            return None, True, True
        except requests.RequestException as e:
            if not self.fail_silently:
                raise
            logger.warning(f"Tracevox request failed (/v1/runs): {e}")
            return None, True, False
        if resp.status_code >= 400:
            message = f"Tracevox API {resp.status_code}: {resp.text[:300]}"
            if not self.fail_silently:
                raise RuntimeError(message)
            logger.warning(message)
            retryable = resp.status_code >= 500 or resp.status_code == 429
            return None, retryable, False
        try:
            run = resp.json().get("run") or {}
        except ValueError:
            return None, True, False
        return (run if run.get("id") else None), True, False

 
    def start_run(
        self,
        run_type: str = "generic",
        name: Optional[str] = None,
        project: str = "default",
        external_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        scenario: Optional[str] = None,
        environment: str = "production",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    ) -> "RunHandle":
        idempotent_external_id = external_id or f"auto_{uuid.uuid4().hex}"
        body = {
            "run_type": run_type,
            "name": name,
            "project": project,
            "external_id": idempotent_external_id,
            "trace_id": trace_id,
            "campaign_id": campaign_id,
            "scenario": scenario,
            "environment": environment,
            "tags": tags or [],
            "metadata": metadata or {},
        }

        run, retryable, was_timeout = self._create_run_once(body)
        if run:
            return RunHandle(self, run["id"], flush_interval=flush_interval)

        if not retryable or self.create_attempts <= 1:
            logger.warning(
                "Tracevox unreachable — run created in offline mode (no-op)."
            )
            return RunHandle(self, None, offline=True, flush_interval=flush_interval)

        logger.warning(
            "TraceVox run creation %s — retrying (1/%d)",
            "timed out" if was_timeout else "failed",
            self.create_attempts,
        )
        handle = RunHandle(self, None, pending=True, flush_interval=flush_interval)
        thread = threading.Thread(
            target=self._retry_create,
            args=(handle, body),
            name="tracevox-run-create-retry",
            daemon=True,
        )
        handle._recovery_thread = thread
        thread.start()
        return handle

    def _retry_create(self, handle: "RunHandle", body: dict) -> None:
        for attempt in range(2, self.create_attempts + 1):
            backoff_idx = min(attempt - 2, len(self.create_backoffs) - 1)
            if self.create_backoffs:
                time.sleep(self.create_backoffs[backoff_idx])
            try:
                run, retryable, was_timeout = self._create_run_once(body)
            except Exception as e: 
                logger.warning(f"Tracevox run creation retry error: {e}")
                run, retryable, was_timeout = None, True, False
            if run:
                handle._activate(run["id"])
                return
            if not retryable:
                break
            if attempt < self.create_attempts:
                logger.warning(
                    "TraceVox run creation %s — retrying (%d/%d)",
                    "timed out" if was_timeout else "failed",
                    attempt,
                    self.create_attempts,
                )
        handle._go_offline()
        logger.warning(
            "TraceVox unavailable after %d attempts — continuing call without "
            "remote telemetry.",
            self.create_attempts,
        )


class RunHandle:

    def __init__(self, client: TracevoxRuns, run_id: Optional[str],
                 offline: bool = False, pending: bool = False,
                 flush_interval: float = DEFAULT_FLUSH_INTERVAL,
                 queue_limits: Optional[Dict[str, int]] = None):
        self.client = client
        self.run_id = run_id or (None if pending else f"offline_{uuid.uuid4().hex[:12]}")
        if offline:
            self._state = _STATE_OFFLINE
        elif pending:
            self._state = _STATE_PENDING
        else:
            self._state = _STATE_ACTIVE
        limits = dict(DEFAULT_QUEUE_LIMITS)
        limits.update(queue_limits or {})
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=limits["events"])
        self._metrics: deque = deque(maxlen=limits["metrics"])
        self._segments: deque = deque(maxlen=limits["segments"])
        self._samples: deque = deque(maxlen=limits["samples"])
        self._findings: deque = deque(maxlen=limits["findings"])
        self._predictions: deque = deque(maxlen=limits["findings"])
        self._completion: Optional[dict] = None
        self._start_monotonic = time.monotonic()
        self._flush_interval = flush_interval
        self._last_flush = time.monotonic()
        self._recovery_thread: Optional[threading.Thread] = None
        atexit.register(self.flush)

    @property
    def state(self) -> str:
        return self._state

    @property
    def offline(self) -> bool:
        return self._state == _STATE_OFFLINE

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        thread = self._recovery_thread
        if thread is not None:
            thread.join(timeout)
        return self._state == _STATE_ACTIVE

    def _queued_count(self) -> int:
        return (len(self._events) + len(self._metrics) + len(self._segments)
                + len(self._samples) + len(self._findings) + len(self._predictions))

    def _activate(self, run_id: str) -> None:
        with self._lock:
            self.run_id = run_id
            self._state = _STATE_ACTIVE
            queued = self._queued_count()
            completion = self._completion
        logger.warning("TraceVox run recovered after retry")
        self.flush()
        logger.warning("TraceVox telemetry queue flushed: %d items", queued)
        if completion is not None:
            self.client._request("POST", f"/v1/runs/{self.run_id}/complete", completion)
            with self._lock:
                self._completion = None

    def _go_offline(self) -> None:
        with self._lock:
            self._state = _STATE_OFFLINE
            if self.run_id is None:
                self.run_id = f"offline_{uuid.uuid4().hex[:12]}"
            self._events.clear()
            self._metrics.clear()
            self._segments.clear()
            self._samples.clear()
            self._findings.clear()
            self._predictions.clear()
            self._completion = None

    def event(
        self,
        event_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        source: str = "",
        severity: str = "info",
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        flush: bool = False,
    ) -> None:
        if self._state == _STATE_OFFLINE:
            return
        payload = {
            "event_type": event_type,
            "event_id": event_id or f"evt_{uuid.uuid4().hex[:20]}",
            "attributes": attributes or {},
            "source": source,
            "severity": severity,
        }
        if timestamp is not None:
            payload["timestamp"] = timestamp
        with self._lock:
            self._events.append(payload)
        self._maybe_flush(force=flush)

    def metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        dimensions: Optional[Dict[str, Any]] = None,
        span_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        flush: bool = False,
    ) -> None:
        if self._state == _STATE_OFFLINE:
            return
        payload = {
            "name": name,
            "value": value,
            "unit": unit,
            "dimensions": dimensions or {},
            "span_id": span_id,
        }
        if timestamp is not None:
            payload["timestamp"] = timestamp
        with self._lock:
            self._metrics.append(payload)
        self._maybe_flush(force=flush)

    def transcript(
        self,
        speaker: str,
        text: str,
        start_time: float,
        end_time: Optional[float] = None,
        source: str = "",
        confidence: Optional[float] = None,
        segment_id: Optional[str] = None,
        flush: bool = False,
    ) -> None:
        if self._state == _STATE_OFFLINE:
            return
        payload = {
            "speaker": speaker,
            "text": text,
            "start_time": start_time,
            "end_time": end_time if end_time is not None else start_time,
            "source": source,
            "confidence": confidence,
        }
        if segment_id is not None:
            payload["segment_id"] = segment_id
        with self._lock:
            self._segments.append(payload)
        self._maybe_flush(force=flush)

    def envelope(self, samples: List[Dict[str, Any]], flush: bool = False) -> None:
        if self._state == _STATE_OFFLINE:
            return
        with self._lock:
            self._samples.extend(samples)
        self._maybe_flush(force=flush)

    def finding(
        self,
        category: str,
        title: str,
        description: str = "",
        severity: str = "medium",
        confidence: float = 0.5,
        failure_attribution: str = "inconclusive",
        evidence: Optional[List[Dict[str, Any]]] = None,
        expected_behavior: str = "",
        impact: str = "",
        finding_id: Optional[str] = None,
    ) -> None:
        if self._state == _STATE_OFFLINE:
            return
        payload = {
            "category": category,
            "title": title,
            "description": description,
            "severity": severity,
            "confidence": confidence,
            "failure_attribution": failure_attribution,
            "evidence": evidence or [],
            "expected_behavior": expected_behavior,
            "impact": impact,
        }
        if finding_id is not None:
            payload["finding_id"] = finding_id
        if self._state == _STATE_PENDING:
            with self._lock:
                self._findings.append(payload)
            return
        self.client._request("POST", f"/v1/runs/{self.run_id}/findings", payload)

    def prediction(
        self,
        risk_score: float,
        predicted_failure_type: Optional[str] = None,
        confidence: float = 0.5,
        signals: Optional[List[Dict[str, Any]]] = None,
        explanation: str = "",
        recommended_action: str = "",
        timestamp: Optional[str] = None,
    ) -> None:
        if self._state == _STATE_OFFLINE:
            return
        payload = {
            "risk_score": risk_score,
            "predicted_failure_type": predicted_failure_type,
            "confidence": confidence,
            "signals": signals or [],
            "explanation": explanation,
            "recommended_action": recommended_action,
        }
        if timestamp is not None:
            payload["timestamp"] = timestamp
        if self._state == _STATE_PENDING:
            with self._lock:
                self._predictions.append(payload)
            return
        self.client._request("POST", f"/v1/runs/{self.run_id}/predictions", payload)

    def flush(self) -> None:
        if self._state == _STATE_OFFLINE:
            with self._lock:
                self._events.clear(); self._metrics.clear()
                self._segments.clear(); self._samples.clear()
                self._findings.clear(); self._predictions.clear()
            return
        if self._state == _STATE_PENDING:
            return  
        with self._lock:
            events = list(self._events); self._events.clear()
            metrics = list(self._metrics); self._metrics.clear()
            segments = list(self._segments); self._segments.clear()
            samples = list(self._samples); self._samples.clear()
            findings = list(self._findings); self._findings.clear()
            predictions = list(self._predictions); self._predictions.clear()
            self._last_flush = time.monotonic()
        if events:
            self.client._request("POST", f"/v1/runs/{self.run_id}/events/batch", {"events": events})
        if metrics:
            self.client._request("POST", f"/v1/runs/{self.run_id}/metrics", {"metrics": metrics})
        if segments:
            self.client._request("POST", f"/v1/runs/{self.run_id}/transcript", {"segments": segments})
        if samples:
            self.client._request("POST", f"/v1/runs/{self.run_id}/envelope", {"samples": samples})
        for payload in findings:
            self.client._request("POST", f"/v1/runs/{self.run_id}/findings", payload)
        for payload in predictions:
            self.client._request("POST", f"/v1/runs/{self.run_id}/predictions", payload)

    def complete(
        self,
        status: str = "completed",
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "status": status,
            "outcome": outcome,
            "metadata": metadata or {},
        }
        if self._state == _STATE_OFFLINE:
            return
        if self._state == _STATE_PENDING:
            with self._lock:
                self._completion = payload
            return
        self.flush()
        self.client._request("POST", f"/v1/runs/{self.run_id}/complete", payload)

    def elapsed(self) -> float:
        return time.monotonic() - self._start_monotonic

    def _maybe_flush(self, force: bool = False) -> None:
        if self._state != _STATE_ACTIVE:
            return  # pending: queue only; offline: no-op
        with self._lock:
            pending = self._queued_count()
            due = (time.monotonic() - self._last_flush) >= self._flush_interval
        if force or pending >= DEFAULT_MAX_BATCH or due:
            self.flush()
