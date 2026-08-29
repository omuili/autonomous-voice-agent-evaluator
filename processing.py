import json
import threading
from pathlib import Path
from typing import Literal

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field

from config import (
    ARTIFACTS_DIR,
    EVAL_MODEL,
    OPENAI_API_KEY,
    TRACEVOX_API_KEY,
    TRACEVOX_GATEWAY_FOR_EVAL,
    TRACEVOX_GATEWAY_URL,
    TRANSCRIPTION_MODEL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)
from scenarios import get_scenario
from storage import call_dir, save_json

import tracevox_integration as tracevox


class BugFinding(BaseModel):
    title: str
    severity: Literal["Critical", "High", "Medium", "Low"]
    category: str
    timestamp: str
    evidence: str
    why_it_matters: str
    expected_behavior: str
    confidence: float = Field(ge=0.0, le=1.0)


class SimulatorIssue(BaseModel):
    title: str
    timestamp: str
    evidence: str
    impact: str
    confidence: float = Field(ge=0.0, le=1.0)


class InfrastructureIssue(BaseModel):
    title: str
    timestamp: str
    evidence: str
    impact: str
    confidence: float = Field(ge=0.0, le=1.0)


class CallEvaluation(BaseModel):
    patient_simulator_speaker: str
    target_agent_speaker: str
    conversation_coherent: bool
    coherence_score: int = Field(ge=1, le=5)
    turn_taking_score: int = Field(ge=1, le=5)
    scenario_goal_reached: bool
    failure_attribution: Literal[
        "none",
        "target_agent",
        "patient_simulator",
        "shared",
        "infrastructure",
        "inconclusive",
    ]
    primary_failure_reason: str
    simulator_quality_notes: list[str]
    simulator_issues: list[SimulatorIssue]
    infrastructure_issues: list[InfrastructureIssue]
    target_agent_strengths: list[str]
    bugs: list[BugFinding]
    summary: str


_openai = OpenAI(api_key=OPENAI_API_KEY)
_bug_report_lock = threading.Lock()

# Which route produced the most recent evaluation ("tracevox_gateway" or
# "openai_direct"). Post-call only; the Realtime WebSocket never touches the
# gateway.
LAST_EVALUATOR_ROUTE = "openai_direct"


def _evaluator_clients() -> list[tuple[str, OpenAI]]:
    clients: list[tuple[str, OpenAI]] = []

    if TRACEVOX_GATEWAY_FOR_EVAL and tracevox.tracevox_enabled():
        clients.append(
            (
                "tracevox_gateway",
                OpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url=TRACEVOX_GATEWAY_URL,
                    default_headers={
                        "X-Tracevox-Key": TRACEVOX_API_KEY,
                    },
                    timeout=60.0,
                    max_retries=0,
                ),
            )
        )

    clients.append(("openai_direct", _openai))
    return clients


def _timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    remaining = seconds - (minutes * 60)
    return f"{minutes:02d}:{remaining:05.2f}"


def download_twilio_recording(recording_url: str, output_path: Path) -> None:
    media_url = recording_url if recording_url.endswith(".mp3") else recording_url + ".mp3"
    response = httpx.get(
        media_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=60.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)


def transcribe_recording(recording_path: Path) -> dict:
    with recording_path.open("rb") as audio_file:
        transcript = _openai.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL,
            file=audio_file,
            response_format="diarized_json",
            chunking_strategy="auto",
        )

    if hasattr(transcript, "model_dump"):
        return transcript.model_dump()
    return json.loads(transcript.json())


def raw_transcript_text(transcript: dict) -> str:
    lines = []
    for segment in transcript.get("segments", []):
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        speaker = str(segment.get("speaker", "Unknown"))
        text = str(segment.get("text", "")).strip()
        lines.append(f"[{_timestamp(start)} - {_timestamp(end)}] {speaker}: {text}")
    return "\n".join(lines)


def load_authoritative_simulator_utterances(directory: Path) -> list[str]:
    path = directory / "events.jsonl"
    if not path.exists():
        return []

    utterances: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if row.get("event_type") != "voice.patient_simulator.transcript":
            continue

        text = str(row.get("payload", {}).get("text", "")).strip()
        if text:
            utterances.append(text)

    return utterances


def evaluate_call(
    scenario_id: str,
    raw_transcript: str,
    authoritative_simulator_utterances: list[str] | None = None,
) -> CallEvaluation:
    scenario = get_scenario(scenario_id)
    authoritative_simulator_utterances = authoritative_simulator_utterances or []

    source_text = "\n".join(
        f"{index}. {text}"
        for index, text in enumerate(authoritative_simulator_utterances, start=1)
    ) or "Not available for this call."

    system_prompt = """
You are a rigorous QA evaluator for a production medical-practice voice agent.

You are evaluating TWO systems:
1. Patient Simulator — our autonomous caller/test harness.
2. Target Agent — the medical-practice receptionist being tested.

Your highest-priority responsibility is CORRECT FAILURE ATTRIBUTION.

Rules:
- Ground every target-agent bug in observable evidence from the diarized transcript.
- Do not blame the Target Agent for a failure caused by the Patient Simulator,
  infrastructure, or uncertain transcription.
- The authoritative Patient Simulator utterances come from the simulator's own
  Realtime output transcript. For what the Patient Simulator intended to say, they
  take precedence over conflicting post-call speech-recognition text.
- The diarized transcript remains the source for timestamps and for what the Target
  Agent said.
- CAUSALITY RULE: post-call transcription or diarization happens AFTER the live call.
  Therefore a post-call transcription mismatch cannot have caused a live target-agent
  lookup, reasoning, scheduling, or conversation failure. Never claim otherwise.
- If authoritative simulator output is correct but the post-call transcript renders it
  incorrectly, classify that as an infrastructure/evidence-quality issue in
  infrastructure_issues. Do NOT classify it as a simulator issue.
- If the Target Agent itself repeats the patient's information correctly, that is
  evidence that the live agent probably understood that information even if the later
  post-call transcript contains a spelling mismatch.
- If the Patient Simulator genuinely changes facts, behaves unnaturally, becomes
  repetitive, fails to steer, over-talks, or reveals the test objective, put that in
  simulator_issues / simulator_quality_notes — never in target-agent bugs.
- bugs must contain ONLY material Target Agent problems. If causation is uncertain,
  do not manufacture a target-agent bug.
- Use failure_attribution='none' when the scenario succeeds without a material
  failure. Otherwise choose the single best primary attribution: target_agent,
  patient_simulator, shared, infrastructure, or inconclusive.
- Use 'shared' only when TWO OR MORE actors/systems materially and causally contributed
  to the live failure. Mere uncertainty, a later transcription discrepancy, or a
  coincidental evidence-quality problem is not enough for 'shared'.
- Use 'infrastructure' when the primary failure is in telephony, streaming, recording,
  transcription, diarization, or another supporting pipeline rather than either
  conversational agent.
- Do not invent clinic policies, hours, insurance participation, system capabilities,
  or facts not supplied by the scenario or transcript.
- Distinguish a genuine product issue from harmless wording preferences.
- Prefer a few high-value findings over many nitpicks. Do not duplicate one root
  cause as multiple bugs merely because it has several symptoms.
- Map anonymous diarization speakers to Patient Simulator and Target Agent based on
  the scenario and conversation behavior.
- If a bug timestamp is cited, use a timestamp shown in the diarized transcript.
- Evaluate turn-taking separately from task completion.
""".strip()

    user_prompt = f"""
SCENARIO ID:
{scenario.id}

SCENARIO TITLE:
{scenario.title}

PATIENT PERSONA:
{scenario.persona}

SOURCE-OF-TRUTH IDENTITY:
Full name: {scenario.full_name}
Exact spelling: {scenario.name_spelling}
Date of birth: {scenario.date_of_birth}
Phone-number rule: {scenario.identity_note}

SITUATION:
{scenario.situation}

PATIENT GOAL:
{scenario.goal}

HIDDEN TEST OBJECTIVE:
{scenario.hidden_test_objective}

EXPECTED TARGET-AGENT BEHAVIOR:
{scenario.expected_behavior}

AUTHORITATIVE PATIENT-SIMULATOR OUTPUT UTTERANCES:
{source_text}

DIARIZED RECORDING TRANSCRIPT:
{raw_transcript}
""".strip()

    global LAST_EVALUATOR_ROUTE
    completion = None
    last_error: Exception | None = None
    for route, client in _evaluator_clients():
        try:
            completion = client.beta.chat.completions.parse(
                model=EVAL_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=CallEvaluation,
            )
            LAST_EVALUATOR_ROUTE = route
            break
        except Exception as exc:
            last_error = exc
            print(f"Evaluator route {route!r} failed, trying next:", repr(exc))

    if completion is None:
        raise RuntimeError("All evaluator routes failed.") from last_error

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("Evaluator returned no parsed result.")
    return parsed


def labeled_transcript_text(transcript: dict, evaluation: CallEvaluation) -> str:
    lines = []
    patient_id = evaluation.patient_simulator_speaker
    target_id = evaluation.target_agent_speaker

    for segment in transcript.get("segments", []):
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        speaker = str(segment.get("speaker", "Unknown"))
        text = str(segment.get("text", "")).strip()

        if speaker == patient_id:
            label = "Patient Simulator"
        elif speaker == target_id:
            label = "Pretty Good AI Agent"
        else:
            label = f"Speaker {speaker}"

        lines.append(f"[{_timestamp(start)} - {_timestamp(end)}] {label}: {text}")

    return "\n".join(lines)


def write_call_report(
    scenario_id: str,
    call_sid: str,
    recording_sid: str,
    evaluation: CallEvaluation,
    directory: Path,
) -> None:
    scenario = get_scenario(scenario_id)
    lines = [
        f"# Call Report — {scenario.title}",
        "",
        f"- Scenario ID: `{scenario_id}`",
        f"- Call SID: `{call_sid}`",
        f"- Recording SID: `{recording_sid}`",
        f"- Coherent conversation: `{evaluation.conversation_coherent}`",
        f"- Coherence score: `{evaluation.coherence_score}/5`",
        f"- Turn-taking score: `{evaluation.turn_taking_score}/5`",
        f"- Scenario goal reached: `{evaluation.scenario_goal_reached}`",
        f"- Primary failure attribution: `{evaluation.failure_attribution}`",
        "",
        "## Summary",
        "",
        evaluation.summary,
        "",
        "## Primary failure reason",
        "",
        evaluation.primary_failure_reason,
        "",
        "## Simulator quality notes",
        "",
    ]

    if evaluation.simulator_quality_notes:
        lines.extend(f"- {note}" for note in evaluation.simulator_quality_notes)
    else:
        lines.append("- None.")

    lines.extend(["", "## Simulator issues", ""])
    if not evaluation.simulator_issues:
        lines.append("No material simulator-induced issues identified.")
    else:
        for index, issue in enumerate(evaluation.simulator_issues, start=1):
            lines.extend([
                f"### S{index}. {issue.title}",
                "",
                f"- Timestamp: `{issue.timestamp}`",
                f"- Confidence: `{issue.confidence:.2f}`",
                "",
                f"**Evidence:** {issue.evidence}",
                "",
                f"**Impact:** {issue.impact}",
                "",
            ])

    lines.extend(["", "## Infrastructure / evidence-quality issues", ""])
    if not evaluation.infrastructure_issues:
        lines.append("No material infrastructure or evidence-quality issues identified.")
    else:
        for index, issue in enumerate(evaluation.infrastructure_issues, start=1):
            lines.extend([
                f"### I{index}. {issue.title}",
                "",
                f"- Timestamp: `{issue.timestamp}`",
                f"- Confidence: `{issue.confidence:.2f}`",
                "",
                f"**Evidence:** {issue.evidence}",
                "",
                f"**Impact:** {issue.impact}",
                "",
            ])

    lines.extend(["", "## Target-agent strengths", ""])
    if evaluation.target_agent_strengths:
        lines.extend(f"- {note}" for note in evaluation.target_agent_strengths)
    else:
        lines.append("- None.")

    lines.extend(["", "## Target-agent bugs", ""])
    if not evaluation.bugs:
        lines.append("No material target-agent bugs identified.")
    else:
        for index, bug in enumerate(evaluation.bugs, start=1):
            lines.extend([
                f"### {index}. {bug.title}",
                "",
                f"- Severity: **{bug.severity}**",
                f"- Category: `{bug.category}`",
                f"- Timestamp: `{bug.timestamp}`",
                f"- Confidence: `{bug.confidence:.2f}`",
                "",
                f"**Evidence:** {bug.evidence}",
                "",
                f"**Why it matters:** {bug.why_it_matters}",
                "",
                f"**Expected behavior:** {bug.expected_behavior}",
                "",
            ])

    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def append_master_bug_report(
    scenario_id: str,
    call_sid: str,
    evaluation: CallEvaluation,
) -> None:
    if not evaluation.bugs:
        return

    path = ARTIFACTS_DIR / "BUG_REPORT.md"
    with _bug_report_lock:
        if not path.exists():
            path.write_text("# Consolidated Target-Agent Bug Report\n\n", encoding="utf-8")

        with path.open("a", encoding="utf-8") as handle:
            for bug in evaluation.bugs:
                handle.write(
                    f"## {bug.title}\n\n"
                    f"- Severity: **{bug.severity}**\n"
                    f"- Scenario: `{scenario_id}`\n"
                    f"- Call: `{call_sid}`\n"
                    f"- Timestamp: `{bug.timestamp}`\n"
                    f"- Category: `{bug.category}`\n"
                    f"- Confidence: `{bug.confidence:.2f}`\n\n"
                    f"**Evidence:** {bug.evidence}\n\n"
                    f"**Why it matters:** {bug.why_it_matters}\n\n"
                    f"**Expected behavior:** {bug.expected_behavior}\n\n"
                )


def process_recording(
    scenario_id: str,
    call_sid: str,
    recording_sid: str,
    recording_url: str,
) -> dict:
    directory = call_dir(scenario_id, call_sid)
    scenario = get_scenario(scenario_id)
    run = tracevox.get_or_start_run(
        call_sid, scenario_id, scenario_title=scenario.title
    )

    recording_path = directory / f"{recording_sid}.mp3"
    transcript_json_path = directory / "transcript.json"
    raw_transcript_path = directory / "transcript_raw.txt"
    transcript_path = directory / "transcript.txt"
    evaluation_path = directory / "evaluation.json"

    download_twilio_recording(recording_url, recording_path)

    run.event(
        "voice.transcription.started",
        {"recording_sid": recording_sid, "model": TRANSCRIPTION_MODEL},
        source="pipeline",
    )
    try:
        transcript = transcribe_recording(recording_path)
    except Exception as exc:
        run.event(
            "voice.transcription.failed",
            {"recording_sid": recording_sid, "error": repr(exc)},
            source="pipeline",
            severity="error",
        )
        raise
    run.event(
        "voice.transcription.completed",
        {
            "recording_sid": recording_sid,
            "segment_count": len(transcript.get("segments", [])),
        },
        source="pipeline",
    )
    save_json(transcript_json_path, transcript)

    raw_text = raw_transcript_text(transcript)
    raw_transcript_path.write_text(raw_text, encoding="utf-8")

    authoritative = load_authoritative_simulator_utterances(directory)

    run.event("voice.evaluation.started", {"model": EVAL_MODEL}, source="pipeline")
    try:
        evaluation = evaluate_call(scenario_id, raw_text, authoritative)
    except Exception as exc:
        run.event(
            "voice.evaluation.failed",
            {"error": repr(exc)},
            source="pipeline",
            severity="error",
        )
        raise
    run.event(
        "voice.evaluation.completed",
        {
            "failure_attribution": evaluation.failure_attribution,
            "scenario_goal_reached": evaluation.scenario_goal_reached,
            "evaluator_route": LAST_EVALUATOR_ROUTE,
        },
        source="pipeline",
    )
    save_json(evaluation_path, evaluation.model_dump())

    transcript_path.write_text(
        labeled_transcript_text(transcript, evaluation),
        encoding="utf-8",
    )

    write_call_report(
        scenario_id,
        call_sid,
        recording_sid,
        evaluation,
        directory,
    )
    append_master_bug_report(scenario_id, call_sid, evaluation)
    tracevox.send_diarized_transcript(
        run,
        transcript,
        evaluation.patient_simulator_speaker,
        evaluation.target_agent_speaker,
    )
    tracevox.send_evaluation_to_tracevox(run, evaluation, scenario_id)
    run.complete(
        status="completed",
        outcome=tracevox.evaluation_outcome(evaluation),
        metadata=tracevox.evaluation_completion_metadata(evaluation),
    )

    return {
        "scenario_id": scenario_id,
        "call_sid": call_sid,
        "recording_sid": recording_sid,
        "recording_path": str(recording_path),
        "transcript_path": str(transcript_path),
        "evaluation_path": str(evaluation_path),
        "report_path": str(directory / "report.md"),
        "bugs_found": len(evaluation.bugs),
        "simulator_issues_found": len(evaluation.simulator_issues),
        "infrastructure_issues_found": len(evaluation.infrastructure_issues),
        "failure_attribution": evaluation.failure_attribution,
    }
