import asyncio
import json
import time
import re
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from websockets.asyncio.client import connect

from config import (
    OPENAI_API_KEY,
    REALTIME_MODEL,
    REALTIME_VOICE,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    websocket_base_url,
)
from processing import process_recording
from scenarios import get_scenario, patient_prompt
from storage import append_event

import tracevox_integration as tracevox



app = FastAPI(title="Autonomous Voice Agent Evaluator", version="1.1.0")

_twilio_control = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
_TERMINAL_GOODBYE = re.compile(
    r"\b(goodbye|bye[- ]?bye|have a (?:good|great|nice) day)\b",
    re.IGNORECASE,
)


def is_terminal_closing(text: str) -> bool:
    return bool(_TERMINAL_GOODBYE.search(text.strip()))


async def complete_twilio_call(state: dict[str, Any]) -> None:
    call_sid = state.get("call_sid")
    if not call_sid or state.get("hangup_requested"):
        return

    state["hangup_requested"] = True

    scenario_id = state.get("scenario_id")
    if scenario_id:
        append_event(
            scenario_id=scenario_id,
            call_sid=call_sid,
            event_type="voice.patient_simulator.call_end_requested",
            payload={
                "reason": "terminal_goodbye_played",
                "text": state.get("last_patient_transcript", ""),
            },
        )

    try:
        await asyncio.to_thread(
            _twilio_control.calls(call_sid).update,
            status="completed",
        )
        print("Call ended after patient simulator goodbye:", call_sid)
    except Exception as exc:
        state["hangup_requested"] = False
        print("Failed to end Twilio call:", repr(exc))



async def connect_to_openai():
    url = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"
    return await connect(
        url,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        max_size=None,
    )


def create_session_update(scenario_id: str) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "output_modalities": ["audio"],
            "audio": {
                "input": {"format": {"type": "audio/pcmu"}},
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": REALTIME_VOICE,
                },
            },
            "instructions": patient_prompt(scenario_id),
        },
    }


@app.get("/")
def home():
    return {"status": "ok", "service": "autonomous-voice-agent"}


@app.post("/voice")
async def voice(
    scenario_id: str = Query(...),
    campaign_id: str | None = Query(default=None),
):
    get_scenario(scenario_id)

    twiml = VoiceResponse()
    connect_node = twiml.connect()
    stream = connect_node.stream(url=f"{websocket_base_url()}/media-stream")
    stream.parameter(name="scenario_id", value=scenario_id)
    if campaign_id:
        stream.parameter(name="campaign_id", value=campaign_id)

    return Response(content=str(twiml), media_type="application/xml")


async def log_event(
    state: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    scenario_id = state.get("scenario_id")
    call_sid = state.get("call_sid")

    if scenario_id and call_sid:
        append_event(
            scenario_id=scenario_id,
            call_sid=call_sid,
            event_type=event_type,
            payload=payload,
        )




async def receive_from_twilio(websocket: WebSocket, openai_connection, state: dict[str, Any]):
    while True:
        message = await websocket.receive_text()
        data = json.loads(message)
        event = data.get("event")

        if event == "connected":
            print("Twilio event: connected")

        elif event == "start":
            start = data["start"]
            state["stream_sid"] = start["streamSid"]
            state["call_sid"] = start["callSid"]
            custom = start.get("customParameters", {})
            state["scenario_id"] = custom.get("scenario_id", "appointment_basic")
            state["campaign_id"] = custom.get("campaign_id") or None
            scenario = get_scenario(state["scenario_id"])

            await openai_connection.send(
                json.dumps(create_session_update(state["scenario_id"]))
            )
            state["session_configured"] = True
            state["call_started_monotonic"] = time.monotonic()

            print(
                "Twilio stream started:",
                state["stream_sid"],
                "scenario:",
                state["scenario_id"],
            )

            await log_event(
                state,
                "voice.stream.started",
                {"media_format": start.get("mediaFormat", {})},
            )

            run = tracevox.get_or_start_run(
                state["call_sid"],
                state["scenario_id"],
                scenario_title=scenario.title,
                campaign_id=state["campaign_id"],
                stream_sid=state["stream_sid"],
            )
            run.mark_media_start()
            state["tvx_run"] = run
            run.event(
                "media.connected",
                {
                    "stream_sid": state["stream_sid"],
                    "media_format": start.get("mediaFormat", {}),
                },
                source="twilio",
            )
            run.event(
                "scenario.initialized",
                {"scenario_id": scenario.id, "scenario_title": scenario.title},
            )

        elif event == "media":
            if not state["session_configured"]:
                continue

            state["input_chunks"] += 1
            state["latest_twilio_timestamp_ms"] = int(data["media"].get("timestamp", "0"))

            await openai_connection.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"],
                    }
                )
            )

            if state["input_chunks"] % 250 == 0:
                print("Audio chunks forwarded to OpenAI:", state["input_chunks"])

            run = state.get("tvx_run")
            if run is not None:
                run.envelope_frame(tracevox.SPEAKER_TARGET, data["media"]["payload"])

        elif event == "mark":
            mark_name = data.get("mark", {}).get("name", "unknown")
            await log_event(state, "voice.playback.mark", {"name": mark_name})

            state["assistant_audio_active"] = False

            if mark_name == state.get("terminal_mark_name"):
                await complete_twilio_call(state)

        elif event == "dtmf":
            digit = data.get("dtmf", {}).get("digit")
            await log_event(state, "voice.dtmf", {"digit": digit})

        elif event == "stop":
            print("Twilio event: stop")
            await log_event(
                state,
                "voice.stream.stopped",
                {
                    "input_chunks": state["input_chunks"],
                    "output_chunks": state["output_chunks"],
                },
            )

            run = state.get("tvx_run")
            if run is not None:
                run.flush_envelope()
                run.event(
                    "media.disconnected",
                    {
                        "input_chunks": state["input_chunks"],
                        "output_chunks": state["output_chunks"],
                    },
                    source="twilio",
                )
                total_turns = state["patient_turns"] + state["target_turns"]
                run.metric(
                    "turn_count",
                    total_turns,
                    dimensions={
                        "patient_simulator": state["patient_turns"],
                        "target_agent": state["target_turns"],
                    },
                )
                run.metric("interruption_count", state["interruption_count"])
                run.metric("cancelled_response_count", state["cancelled_responses"])
                run.metric("completed_response_count", state["completed_responses"])
            break


async def receive_from_openai(openai_connection, websocket: WebSocket, state: dict[str, Any]):
    async for message in openai_connection:
        data = json.loads(message)
        event_type = data.get("type")

        if event_type == "session.updated":
            print("OpenAI session configured")

        elif event_type == "input_audio_buffer.speech_started":
            state["speech_started_at"] = time.monotonic()
            interrupted = bool(state.get("assistant_audio_active"))

            if state.get("stream_sid"):
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "clear",
                            "streamSid": state["stream_sid"],
                        }
                    )
                )

            await log_event(state, "voice.incoming_speech.started", {})

            run = state.get("tvx_run")
            if run is not None:
                if interrupted:
                    state["interruption_count"] += 1
                    run.event(
                        "voice.interruption.detected",
                        {"response_id": state.get("current_response_id")},
                        source="openai",
                    )
                run.event(
                    "voice.speech.started",
                    {"speaker": tracevox.SPEAKER_TARGET},
                    source="openai",
                )
                if state.get("stream_sid"):
                    run.event(
                        "voice.playback.cleared",
                        {"had_active_audio": interrupted},
                    )
            state["assistant_audio_active"] = False

        elif event_type == "input_audio_buffer.speech_stopped":
            state["speech_stopped_at"] = time.monotonic()
            state["target_turns"] += 1
            await log_event(state, "voice.incoming_speech.stopped", {})

            run = state.get("tvx_run")
            if run is not None:
                run.event(
                    "voice.speech.stopped",
                    {"speaker": tracevox.SPEAKER_TARGET},
                    source="openai",
                )

        elif event_type == "response.created":
            response = data.get("response", {})
            state["current_response_id"] = response.get("id")
            state["first_audio_for_response"] = True

            run = state.get("tvx_run")
            if run is not None:
                run.event(
                    "voice.response.created",
                    {"response_id": response.get("id")},
                    source="openai",
                )

        elif event_type == "response.output_audio.delta":
            stream_sid = state.get("stream_sid")
            if not stream_sid:
                continue

            await websocket.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": data["delta"]},
                    }
                )
            )
            state["output_chunks"] += 1
            state["assistant_audio_active"] = True

            run = state.get("tvx_run")
            if run is not None:
                run.envelope_frame(tracevox.SPEAKER_PATIENT, data["delta"])

            if state["first_audio_for_response"]:
                state["first_audio_for_response"] = False
                stopped = state.get("speech_stopped_at")
                latency_ms = None
                if stopped is not None:
                    latency_ms = round((time.monotonic() - stopped) * 1000, 1)

                print(
                    "First OpenAI audio chunk sent to Twilio",
                    f"latency_ms={latency_ms}",
                )
                await log_event(
                    state,
                    "voice.response.first_audio",
                    {
                        "response_id": state.get("current_response_id"),
                        "latency_ms": latency_ms,
                    },
                )

                if run is not None:
                    run.event(
                        "voice.response.first_audio",
                        {
                            "response_id": state.get("current_response_id"),
                            "latency_ms": latency_ms,
                            "scenario_id": state.get("scenario_id"),
                        },
                        source="openai",
                    )
                    if latency_ms is not None:
                        run.metric(
                            "response_latency_ms",
                            latency_ms,
                            unit="ms",
                            dimensions={
                                "response_id": state.get("current_response_id"),
                                "scenario_id": state.get("scenario_id"),
                            },
                        )

        elif event_type == "response.output_audio_transcript.done":
            transcript_text = str(data.get("transcript", "")).strip()
            state["last_patient_transcript"] = transcript_text
            state["patient_turns"] += 1

            await log_event(
                state,
                "voice.patient_simulator.transcript",
                {
                    "response_id": state.get("current_response_id"),
                    "turn": state["patient_turns"],
                    "text": transcript_text,
                },
            )

            run = state.get("tvx_run")
            if run is not None and transcript_text:
                run.transcript(
                    speaker="Patient Simulator",
                    text=transcript_text,
                    start_time=round(run.elapsed(), 2),
                    source="realtime_output",
                )

                for signal_type, attributes in tracevox.detect_progress_signals(
                    transcript_text
                ):
                    if signal_type == "patient.declared_new_patient":
                        if state["new_patient_declared"]:
                            continue
                        state["new_patient_declared"] = True
                    run.event(
                        signal_type,
                        {**attributes, "utterance": transcript_text},
                        source="patient_simulator",
                    )

            if transcript_text and is_terminal_closing(transcript_text):
                state["end_call_after_response"] = True
                await log_event(
                    state,
                    "voice.patient_simulator.terminal_closing",
                    {
                        "response_id": state.get("current_response_id"),
                        "text": transcript_text,
                    },
                )
                if run is not None:
                    run.event(
                        "scenario.call_closing",
                        {"response_id": state.get("current_response_id")},
                        source="patient_simulator",
                    )

        elif event_type == "response.output_audio.done":
            stream_sid = state.get("stream_sid")
            if stream_sid:
                mark_name = "response-" + str(state.get("current_response_id", "unknown"))

                if state.get("end_call_after_response"):
                    state["terminal_mark_name"] = mark_name
                    state["end_call_after_response"] = False

                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "mark",
                            "streamSid": stream_sid,
                            "mark": {"name": mark_name},
                        }
                    )
                )

        elif event_type == "response.done":
            response = data.get("response", {})
            response_status = response.get("status")
            await log_event(
                state,
                "voice.response.done",
                {
                    "response_id": response.get("id"),
                    "status": response_status,
                },
            )

            run = state.get("tvx_run")
            if run is not None:
                if response_status == "cancelled":
                    state["cancelled_responses"] += 1
                    run.event(
                        "voice.response.cancelled",
                        {"response_id": response.get("id")},
                        source="openai",
                    )
                else:
                    state["completed_responses"] += 1
                    run.event(
                        "voice.response.completed",
                        {"response_id": response.get("id"), "status": response_status},
                        source="openai",
                    )

        elif event_type == "error":
            print("OPENAI ERROR:", json.dumps(data, indent=2))
            await log_event(state, "voice.openai.error", {"error": data.get("error", data)})

            run = state.get("tvx_run")
            if run is not None:
                error = data.get("error", {}) or {}
                run.event(
                    "system.error",
                    {
                        "component": "openai_realtime",
                        "error_type": error.get("type"),
                        "message": error.get("message"),
                    },
                    source="openai",
                    severity="error",
                )


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    print("Twilio WebSocket connected")

    openai_connection = None
    twilio_task = None
    openai_task = None

    state: dict[str, Any] = {
        "stream_sid": None,
        "call_sid": None,
        "scenario_id": None,
        "session_configured": False,
        "input_chunks": 0,
        "output_chunks": 0,
        "latest_twilio_timestamp_ms": 0,
        "speech_started_at": None,
        "speech_stopped_at": None,
        "current_response_id": None,
        "first_audio_for_response": False,
        "call_started_monotonic": None,
        "patient_turns": 0,
        "last_patient_transcript": "",
        "end_call_after_response": False,
        "terminal_mark_name": None,
        "hangup_requested": False,
        "campaign_id": None,
        "tvx_run": None,
        "assistant_audio_active": False,
        "interruption_count": 0,
        "cancelled_responses": 0,
        "completed_responses": 0,
        "target_turns": 0,
        "new_patient_declared": False,
    }

    try:
        openai_connection = await connect_to_openai()
        print("OpenAI WebSocket connected")

        twilio_task = asyncio.create_task(
            receive_from_twilio(websocket, openai_connection, state)
        )
        openai_task = asyncio.create_task(
            receive_from_openai(openai_connection, websocket, state)
        )

        done, pending = await asyncio.wait(
            {twilio_task, openai_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            exc = task.exception()
            if exc:
                raise exc

        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        print("Twilio WebSocket disconnected")

    except Exception as exc:
        print("Media stream error:", repr(exc))
        try:
            await log_event(state, "voice.stream.error", {"error": repr(exc)})
        except Exception:
            pass
        run = state.get("tvx_run")
        if run is not None:
            run.event(
                "system.error",
                {"component": "media_stream", "message": repr(exc)},
                severity="error",
            )

    finally:
        for task in (twilio_task, openai_task):
            if task is not None and not task.done():
                task.cancel()

        if openai_connection is not None:
            try:
                await openai_connection.close()
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass

        print("Media stream closed")


_TWILIO_STATUS_EVENTS = {
    "queued": "call.initiated",
    "initiated": "call.initiated",
    "ringing": "call.ringing",
    "in-progress": "call.answered",
    "completed": "call.completed",
    "busy": "call.failed",
    "failed": "call.failed",
    "no-answer": "call.failed",
    "canceled": "call.failed",
}


@app.post("/call-status")
async def call_status(
    request: Request,
    scenario_id: str = Query(...),
    campaign_id: str | None = Query(default=None),
):
    form = await request.form()
    payload = dict(form)

    call_sid = str(payload.get("CallSid", "unknown"))
    status = str(payload.get("CallStatus", "unknown"))

    if call_sid != "unknown":
        append_event(
            scenario_id,
            call_sid,
            "twilio.call.status",
            {
                "status": status,
                "payload": {k: str(v) for k, v in payload.items()},
            },
        )

        try:
            scenario = get_scenario(scenario_id)
            run = tracevox.get_or_start_run(
                call_sid,
                scenario_id,
                scenario_title=scenario.title,
                campaign_id=campaign_id,
            )
            event_type = _TWILIO_STATUS_EVENTS.get(status)
            if event_type:
                run.event(event_type, {"twilio_status": status}, source="twilio")

            if event_type == "call.failed":
                run.complete(
                    status="completed",
                    outcome="inconclusive",
                    metadata={
                        "primary_failure_attribution": "infrastructure",
                        "reason": f"telephone call {status}",
                    },
                )
        except Exception as exc:
            print("TraceVox call-status instrumentation failed:", repr(exc))

    return {"ok": True}


def process_recording_task(
    scenario_id: str,
    call_sid: str,
    recording_sid: str,
    recording_url: str,
) -> None:
    try:
        result = process_recording(
            scenario_id=scenario_id,
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
        )
        print("Recording processed:", result)
    except Exception as exc:
        print("Recording processing failed:", repr(exc))
        try:
            run = tracevox.get_run(call_sid) or tracevox.get_or_start_run(
                call_sid, scenario_id
            )
            run.event(
                "system.error",
                {"component": "post_call_processing", "message": repr(exc)},
                source="pipeline",
                severity="error",
            )
            run.complete(
                status="failed",
                outcome="inconclusive",
                metadata={
                    "primary_failure_attribution": "infrastructure",
                    "reason": "post-call processing failed",
                    "error": repr(exc),
                },
            )
        except Exception as tvx_exc:
            print("TraceVox failure reporting failed:", repr(tvx_exc))


@app.post("/recording-complete")
async def recording_complete(
    request: Request,
    background_tasks: BackgroundTasks,
    scenario_id: str = Query(...),
):
    form = await request.form()

    call_sid = str(form.get("CallSid", ""))
    recording_sid = str(form.get("RecordingSid", ""))
    recording_url = str(form.get("RecordingUrl", ""))
    recording_status = str(form.get("RecordingStatus", ""))

    if recording_status != "completed":
        return {"accepted": False, "status": recording_status}

    if not all([call_sid, recording_sid, recording_url]):
        return {"accepted": False, "error": "Missing recording callback fields"}

    append_event(
        scenario_id,
        call_sid,
        "twilio.recording.completed",
        {"recording_sid": recording_sid},
    )

    try:
        run = tracevox.get_run(call_sid)
        if run is not None:
            run.event(
                "voice.recording.completed",
                {"recording_sid": recording_sid},
                source="twilio",
            )
    except Exception as exc:
        print("TraceVox recording instrumentation failed:", repr(exc))

    background_tasks.add_task(
        process_recording_task,
        scenario_id,
        call_sid,
        recording_sid,
        recording_url,
    )

    return {
        "accepted": True,
        "call_sid": call_sid,
        "recording_sid": recording_sid,
    }
