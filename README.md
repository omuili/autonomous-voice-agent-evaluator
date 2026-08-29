# Autonomous Voice-Agent Evaluator

I built this project for the Good AI voice-agent challenge to evaluate a production-style AI medical receptionist through real phone conversations rather than a scripted benchmark. My caller uses Twilio for telephony and OpenAI Realtime for low-latency speech-to-speech patient simulation. I designed the scenarios to behave like realistic patients: they reveal information naturally, respond to the receptionist, correct misunderstandings, interrupt when the scenario calls for it, and continue steering toward the intended task without reading from a fixed script.

Each call is recorded in dual channel, transcribed after the call, and evaluated against the scenario goal. I keep the recording, timestamped transcript, structured evaluation, and report as separate evidence so I can inspect what actually happened before accepting a finding. I also integrated TraceVox, an AI observability and assurance platform I developed, to monitor the autonomous interaction independently of the live audio path. TraceVox gives me runtime visibility into call events, response latency, interruptions, speaker activity, risk signals, and post-call findings while remaining failure-isolated from the conversation itself.

## What I built

- Real outbound AI-to-AI telephone conversations.
- A natural patient simulator using OpenAI Realtime speech-to-speech.
- Scenario coverage for scheduling, rescheduling, cancellation, medication refill, office information, insurance, ambiguity, correction, interruptions, and unusual identity cases.
- Bidirectional Twilio Media Streams with PCMU audio.
- Voice activity detection and barge-in handling.
- Dual-channel MP3 recordings.
- Timestamped diarized transcripts.
- Structured post-call evaluation with separate attribution for target-agent, simulator, shared, infrastructure, inconclusive, and no-failure outcomes.
- Human-reviewed bug evidence rather than unfiltered model-generated findings.
- A hard destination allowlist that restricts outbound calls to the challenge-provided number.
- TraceVox Autonomous Runs instrumentation as an independent assurance layer.

## Architecture

The live conversation path is intentionally short: **Pretty Good AI ↔ Twilio Voice/Media Streams ↔ FastAPI/Python ↔ OpenAI Realtime**. Twilio carries the phone call and streams PCMU audio to my FastAPI backend, which bridges that audio to OpenAI Realtime. I chose Realtime speech-to-speech instead of a sequential speech-to-text → language model → text-to-speech pipeline because I wanted lower turn latency, built-in voice activity detection, and practical interruption handling during a real phone conversation.

The evidence and assurance paths run alongside the conversation rather than inside it. Twilio produces a dual-channel recording after the call; I diarize and timestamp that recording, evaluate it against the scenario, and review the resulting findings before consolidating them. TraceVox receives asynchronous runtime and post-call telemetry as a separate assurance plane. A TraceVox timeout or outage cannot block Twilio or OpenAI Realtime, and the local recording/transcript/report pipeline remains available independently. A short architecture note is included in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Repository layout

```text
.
├── main.py
├── call.py
├── config.py
├── scenarios.py
├── processing.py
├── storage.py
├── run_campaign.py
├── reevaluate.py
├── tracevox_integration.py
├── tracevox_runs_client.py
├── requirements.txt
├── .env.example
├── .gitignore
├── tests/
├── evidence/
├── README.md
├── ARCHITECTURE.md
├── FINAL_BUG_REPORT.md
└── CALL_SELECTION.md
```

## Local configuration

I keep Twilio, OpenAI, and TraceVox credentials in a local `.env` file. The file is excluded from version control, while `.env.example` documents the required variable names.

```text
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
OPENAI_API_KEY=
PUBLIC_BASE_URL=https://your-ngrok-domain.ngrok-free.dev

REALTIME_MODEL=gpt-realtime-2.1
REALTIME_VOICE=marin
TRANSCRIPTION_MODEL=gpt-4o-transcribe-diarize

TRACEVOX_ENABLED=true
TRACEVOX_BASE_URL=https://api.tracevox.ai
TRACEVOX_API_KEY=
TRACEVOX_CAMPAIGN_ID=good-ai-voice-eval-2026
TRACEVOX_ENVIRONMENT=development
TRACEVOX_GATEWAY_FOR_EVAL=true
```

I used one Twilio originating number for the final challenge calls, as required. The destination is hard-allowlisted in the application, so the challenge caller cannot be used to dial arbitrary numbers.

## Running the application

I run the service locally with Uvicorn and expose the Twilio webhook through ngrok:

```bash
uvicorn main:app --reload
```

```bash
ngrok http 8000
```

A single scenario can then be run with:

```bash
python call.py --scenario appointment_basic --wait
```

For the TraceVox campaign grouping I used:

```bash
python call.py \
  --scenario identity_isolation \
  --campaign-id good-ai-voice-eval-2026 \
  --wait
```

## Scenario coverage

My final evidence set contains 10 selected calls:

1. `appointment_basic` — new-patient appointment scheduling.
2. `reschedule` — moving an existing appointment.
3. `cancel` — cancellation request.
4. `refill` — medication refill request.
5. `office_hours_weekend` — weekend/office-hours inquiry.
6. `location` — office location/directions.
7. `insurance` — insurance inquiry.
8. `ambiguous_request` — unclear request requiring clarification.
9. `barge_in` — interruption and turn-taking behavior.
10. `identity_isolation` — new-patient identity isolation edge case.

I completed additional development and validation calls, but I selected these 10 because together they provide the clearest coverage of the challenge requirements and the strongest mix of successful and unsuccessful behavior. The exact call IDs are listed in [`CALL_SELECTION.md`](CALL_SELECTION.md).

## Evidence

For the public submission I organized each selected call under `evidence/` with the files a reviewer needs to inspect the interaction directly:

```text
evidence/
└── <scenario>/
    ├── call.mp3
    ├── transcript.txt
    └── report.md
```

I retained the lower-level JSON and internal event artifacts locally, but I did not make the public evidence folder depend on them. The MP3 recording is the primary source, the transcript makes the call easy to review, and the report summarizes the scenario outcome and findings.

## Evaluation approach

I use the post-call diarized transcript for timestamps and target-agent speech. I also retain the patient simulator's own Realtime output transcript as a second source for what my caller intended to say. This distinction became important around spelled names, because a post-call speech recognizer can mishear individual letters even when the live agent heard them correctly.

I classify the primary outcome as one of:

- `target_agent`
- `patient_simulator`
- `shared`
- `infrastructure`
- `inconclusive`
- `none`

I do not automatically treat every model-generated finding as a bug. I reviewed the final evidence for causality and consistency before including a finding in [`FINAL_BUG_REPORT.md`](FINAL_BUG_REPORT.md). That review also lets me keep successful calls as positive controls rather than forcing every scenario to produce a defect.

## Main findings

### Rejected caller-ID identity can be reintroduced after correction

In the identity-isolation call, the caller explicitly says she is a new patient, rejects a phone-number association, and asks the receptionist to continue with verified name and date of birth. The agent later reintroduces the rejected phone number. I consider this the strongest finding because the problem is not simply initial caller-ID lookup; it is failure to reliably discard identity data after the caller has rejected it.

### New-patient scheduling can fall back into an existing-record workflow

In the new-patient scheduling call, the patient clearly states that she is new and wants an appointment. After identity clarification, the interaction returns to an information-lookup path and transfers the caller without discussing appointment availability or completing a concrete scheduling step.

### Identity verification can become repetitive

Across multiple calls, the agent returns to phone-number, spelling, and date-of-birth checks after the caller has already explained that the phone number cannot be confirmed and has provided alternative identity information. The result is longer calls and unnecessary state repetition.

The evidence also includes calls where the target agent behaved appropriately. For example, it handled location and office-hours questions coherently, asked for clarification on an ambiguous request, and avoided claiming a cancellation or insurance outcome that it could not verify.

## TraceVox assurance integration

TraceVox is an AI observability and assurance platform I developed independently of this challenge. I connected it to this voice evaluator because I wanted to test whether an assurance layer could observe an autonomous voice interaction while it was happening, correlate runtime behavior with the final outcome, and help identify where the target application could be improved.

Each Twilio call is represented as a TraceVox **Autonomous Run** using the Twilio Call SID as the external correlation identifier. The voice application can send call lifecycle events, media-state events, speech start/stop signals, first-audio latency, interruptions, response completions/cancellations, downsampled speaker activity and audio-envelope measurements, live simulator transcript segments, final diarized transcript segments, evaluation metrics, attributed findings, and the final run outcome.

I deliberately kept TraceVox outside the critical audio path. The integration uses background workers, bounded queues, idempotent run creation, retry logic, and fail-silent behavior. If TraceVox is slow or unavailable, the phone conversation continues and the local evidence pipeline still completes. This separation lets TraceVox add observability without becoming another dependency required for the call to succeed.

The current Early Warning layer in TraceVox is an explainable deterministic system rather than a trained predictive model. It combines observable run signals into a risk trajectory, records the factors that increased risk, and can recommend a recovery action. In this project, that assurance layer is useful for exposing developing problems such as repeated identity corrections, stalled progress, interruption patterns, or latency degradation. I see this as a way TraceVox can make autonomous applications such as voice agents easier to diagnose and improve.

## Testing

I run the TraceVox integration tests with:

```bash
python -m pytest tests/test_tracevox_integration.py -q
```

The integration tests use mocks and do not place paid telephone calls.

## Known limitations

- Post-call diarization can occasionally misread spelled letters or misattribute transferred audio, which is why I review important findings against the recording and channel context.
- Live semantic transcription of the target receptionist is not yet part of the realtime assurance feed; the full two-sided semantic analysis currently uses the post-call diarized recording.
- TraceVox adds assurance and observability, but the challenge evidence does not depend on it. Recordings, transcripts, and reports remain available locally if TraceVox is unavailable.

## Submission files

- [`evidence/`](evidence/) — 10 selected calls with MP3 recording, transcript, and report.
- [`FINAL_BUG_REPORT.md`](FINAL_BUG_REPORT.md) — consolidated human-reviewed findings.
- [`CALL_SELECTION.md`](CALL_SELECTION.md) — exact selected-call manifest.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — concise architecture and tradeoff note.

