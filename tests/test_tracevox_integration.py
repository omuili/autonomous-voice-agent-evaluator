import base64
from types import SimpleNamespace

import pytest

import tracevox_integration as tvx
from call import build_callback_urls


class FakeHandle:
    def __init__(self):
        self.events = []
        self.metrics = []
        self.segments = []
        self.samples = []
        self.findings = []
        self.completions = []
        self.flush_count = 0

    def event(self, event_type, attributes=None, source="", severity="info", **kw):
        self.events.append(
            {
                "event_type": event_type,
                "attributes": attributes or {},
                "source": source,
                "severity": severity,
            }
        )

    def metric(self, name, value, unit="", dimensions=None, **kw):
        self.metrics.append(
            {"name": name, "value": value, "unit": unit, "dimensions": dimensions or {}}
        )

    def transcript(
        self,
        speaker,
        text,
        start_time,
        end_time=None,
        source="",
        confidence=None,
        segment_id=None,
        **kw,
    ):
        self.segments.append(
            {
                "speaker": speaker,
                "text": text,
                "start_time": start_time,
                "end_time": end_time,
                "source": source,
                "segment_id": segment_id,
            }
        )

    def envelope(self, samples, **kw):
        self.samples.extend(samples)

    def finding(self, **kwargs):
        self.findings.append(kwargs)

    def complete(self, status="completed", outcome=None, metadata=None):
        self.completions.append(
            {"status": status, "outcome": outcome, "metadata": metadata or {}}
        )

    def flush(self):
        self.flush_count += 1


class FakeClient:
    def __init__(self, holder):
        self.holder = holder

    def start_run(self, **kwargs):
        self.holder["start_calls"].append(kwargs)
        handle = FakeHandle()
        self.holder["handles"].append(handle)
        return handle

@pytest.fixture(autouse=True)
def restore_adapter_state():
    snapshot = dict(tvx._settings)
    original_factory = tvx.client_factory
    tvx._reset_for_tests()
    yield
    tvx._reset_for_tests()
    tvx._settings.update(snapshot)
    tvx.client_factory = original_factory


@pytest.fixture
def fake_tracevox():
    tvx.configure(
        enabled=True,
        api_key="sk_test",
        base_url="https://tracevox.invalid",
        environment="test",
        default_campaign_id="",
    )
    holder = {"start_calls": [], "handles": []}
    tvx.client_factory = lambda: FakeClient(holder)
    return holder


def finish(run, holder):
    run.complete()
    run.join(timeout=5.0)
    assert holder["handles"], "run worker never created a TraceVox handle"
    return holder["handles"][-1]

def test_disabled_flag_is_total_noop():
    tvx.configure(enabled=False, api_key="sk_test")
    tvx.client_factory = lambda: pytest.fail("client must not be created when disabled")

    run = tvx.get_or_start_run("CA_disabled", "appointment_basic")
    assert run.enabled is False

    run.event("call.answered")
    run.metric("response_latency_ms", 900)
    run.transcript("Patient Simulator", "hello", 1.0)
    run.envelope_frame(tvx.SPEAKER_TARGET, base64.b64encode(b"\xff" * 160).decode())
    run.finding(category="x", title="y")
    run.complete(outcome="success")
    run.join(timeout=1.0)

    assert tvx.get_run("CA_disabled") is None


def test_missing_api_key_is_total_noop():
    tvx.configure(enabled=True, api_key="")
    tvx.client_factory = lambda: pytest.fail("client must not be created without a key")

    run = tvx.get_or_start_run("CA_nokey", "appointment_basic")
    assert run.enabled is False
    run.event("call.answered")
    run.complete()


def test_network_failure_does_not_break_call_logic():
    tvx.configure(
        enabled=True,
        api_key="sk_test",
        base_url="http://127.0.0.1:9",
        environment="test",
        default_campaign_id="",
    )

    run = tvx.get_or_start_run("CA_netfail", "appointment_basic")
    run.event("call.answered")
    run.metric("response_latency_ms", 1200)
    run.transcript("Patient Simulator", "hello", 0.5)
    run.finding(category="identity_state", title="t")
    run.complete(outcome="failure")
    run.join(timeout=10.0)


def test_run_creation_payload(fake_tracevox):
    run = tvx.get_or_start_run(
        "CA123",
        "identity_isolation",
        scenario_title="New-patient caller identity isolation",
        campaign_id="camp-1",
        stream_sid="MZ99",
    )
    finish(run, fake_tracevox)

    assert len(fake_tracevox["start_calls"]) == 1
    call = fake_tracevox["start_calls"][0]
    assert call["run_type"] == "voice"
    assert call["external_id"] == "CA123"
    assert call["scenario"] == "identity_isolation"
    assert call["campaign_id"] == "camp-1"
    metadata = call["metadata"]
    assert metadata["application"] == "autonomous-voice-agent"
    assert metadata["test_type"] == "voice_agent_evaluation"
    assert metadata["target_system"] == "Pretty Good AI"
    assert metadata["twilio_call_sid"] == "CA123"
    assert metadata["twilio_stream_sid"] == "MZ99"
    assert metadata["scenario_id"] == "identity_isolation"
    assert "scenario_title" in metadata
    flattened = str(metadata).lower()
    assert "auth" not in flattened
    assert "sk_" not in flattened


def test_registry_returns_same_run_for_same_call_sid(fake_tracevox):
    run_a = tvx.get_or_start_run("CA_same", "appointment_basic")
    run_b = tvx.get_or_start_run("CA_same", "appointment_basic")
    assert run_a is run_b
    finish(run_a, fake_tracevox)


def test_default_campaign_from_settings(fake_tracevox):
    tvx.configure(default_campaign_id="good-ai-voice-eval-2026")
    run = tvx.get_or_start_run("CA_camp", "appointment_basic")
    finish(run, fake_tracevox)
    assert fake_tracevox["start_calls"][0]["campaign_id"] == "good-ai-voice-eval-2026"


def test_event_emission(fake_tracevox):
    run = tvx.get_or_start_run("CA_evt", "appointment_basic")
    run.event("call.answered", {"twilio_status": "in-progress"}, source="twilio")
    run.event("voice.interruption.detected", severity="info", source="openai")
    handle = finish(run, fake_tracevox)

    types = [e["event_type"] for e in handle.events]
    assert types == ["call.answered", "voice.interruption.detected"]
    assert handle.events[0]["attributes"] == {"twilio_status": "in-progress"}
    assert handle.events[0]["source"] == "twilio"


def test_metric_emission(fake_tracevox):
    run = tvx.get_or_start_run("CA_met", "appointment_basic")
    run.metric("response_latency_ms", 1284.5, unit="ms", dimensions={"response_id": "r1"})
    handle = finish(run, fake_tracevox)

    assert handle.metrics == [
        {
            "name": "response_latency_ms",
            "value": 1284.5,
            "unit": "ms",
            "dimensions": {"response_id": "r1"},
        }
    ]


def test_completion_outcome_and_double_complete(fake_tracevox):
    run = tvx.get_or_start_run("CA_done", "appointment_basic")
    run.complete(outcome="partial", metadata={"scenario_goal_reached": True})
    run.complete(outcome="failure")
    run.join(timeout=5.0)

    handle = fake_tracevox["handles"][-1]
    assert len(handle.completions) == 1
    assert handle.completions[0]["outcome"] == "partial"
    assert handle.completions[0]["metadata"]["scenario_goal_reached"] is True
    assert tvx.get_run("CA_done") is None
    run.event("late.event")

def test_mulaw_decode_table():
    assert tvx._MULAW_NORM[0xFF] == 0.0                       
    assert abs(tvx._MULAW_NORM[0x00]) == pytest.approx(32124 / 32768.0)


def test_envelope_bucketing_and_speech_activity(fake_tracevox):
    run = tvx.get_or_start_run("CA_env", "appointment_basic")
    silence = base64.b64encode(b"\xff" * 160).decode()
    loud = base64.b64encode(b"\x00" * 160).decode()

    run.envelope_frame(tvx.SPEAKER_TARGET, silence, t=0.10)
    run.envelope_frame(tvx.SPEAKER_TARGET, silence, t=0.12)
    run.envelope_frame(tvx.SPEAKER_PATIENT, loud, t=0.60)
    run.flush_envelope()
    handle = finish(run, fake_tracevox)

    assert len(handle.samples) == 2
    by_speaker = {s["speaker"]: s for s in handle.samples}

    target = by_speaker[tvx.SPEAKER_TARGET]
    assert target["t"] == 0.0
    assert target["rms"] == 0.0
    assert target["speech_active"] is False

    patient = by_speaker[tvx.SPEAKER_PATIENT]
    assert patient["t"] == 0.5
    assert patient["rms"] > 0.9
    assert patient["peak"] > 0.9
    assert patient["speech_active"] is True


def test_envelope_ignores_garbage_payload(fake_tracevox):
    run = tvx.get_or_start_run("CA_garb", "appointment_basic")
    run.envelope_frame(tvx.SPEAKER_TARGET, "!!!not-base64!!!", t=0.1)
    run.flush_envelope()
    handle = finish(run, fake_tracevox)
    assert handle.samples == []


def test_diarized_transcript_conversion(fake_tracevox):
    run = tvx.get_or_start_run("CA_tx", "appointment_basic")
    transcript = {
        "segments": [
            {"speaker": "A", "text": "Hi, I'm a new patient.", "start": 1.0, "end": 3.5},
            {"speaker": "B", "text": "Welcome! Your name?", "start": 4.0, "end": 6.0},
            {"speaker": "C", "text": "background voice", "start": 7.0, "end": 7.5},
            {"speaker": "A", "text": "", "start": 8.0, "end": 8.1}, 
        ]
    }
    tvx.send_diarized_transcript(run, transcript, patient_speaker="A", target_speaker="B")
    handle = finish(run, fake_tracevox)

    assert len(handle.segments) == 3
    assert handle.segments[0]["speaker"] == "Patient Simulator"
    assert handle.segments[0]["segment_id"] == "seg_0000"
    assert handle.segments[0]["source"] == "post_call_diarization"
    assert handle.segments[0]["start_time"] == 1.0
    assert handle.segments[0]["end_time"] == 3.5
    assert handle.segments[1]["speaker"] == "Pretty Good AI Agent"
    assert handle.segments[2]["speaker"] == "Speaker C"


def _sample_evaluation():
    return SimpleNamespace(
        patient_simulator_speaker="A",
        target_agent_speaker="B",
        conversation_coherent=True,
        coherence_score=4,
        turn_taking_score=5,
        scenario_goal_reached=False,
        failure_attribution="target_agent",
        bugs=[
            SimpleNamespace(
                title="Agent reused rejected phone identity",
                severity="High",
                category="identity_state",
                timestamp="00:44.00",
                evidence="Agent reasserted the rejected number.",
                why_it_matters="Cross-patient identity leakage.",
                expected_behavior="Reset identity state after rejection.",
                confidence=0.9,
            )
        ],
        simulator_issues=[
            SimpleNamespace(
                title="Simulator repeated itself",
                timestamp="01:02.00",
                evidence="Same sentence twice.",
                impact="Minor realism loss.",
                confidence=0.6,
            )
        ],
        infrastructure_issues=[
            SimpleNamespace(
                title="Diarization mis-heard spelled name",
                timestamp="00:20.00",
                evidence="Transcript renders E-L-E-N-A incorrectly.",
                impact="Evidence quality only.",
                confidence=0.7,
            )
        ],
    )


def test_finding_conversion_and_attribution(fake_tracevox):
    run = tvx.get_or_start_run("CA_find", "identity_isolation")
    tvx.send_evaluation_to_tracevox(run, _sample_evaluation(), "identity_isolation")
    handle = finish(run, fake_tracevox)

    assert len(handle.findings) == 3
    by_attr = {f["failure_attribution"]: f for f in handle.findings}

    bug = by_attr["target_agent"]
    assert bug["severity"] == "high"
    assert bug["category"] == "identity_state"
    assert bug["confidence"] == 0.9
    assert bug["expected_behavior"] == "Reset identity state after rejection."
    assert bug["impact"] == "Cross-patient identity leakage."
    assert bug["evidence"][0]["ref"] == "00:44.00"

    assert by_attr["simulator"]["category"] == "simulator_behavior"
    assert by_attr["infrastructure"]["category"] == "infrastructure"

    created = [e for e in handle.events if e["event_type"] == "assurance.finding.created"]
    assert len(created) == 3

    metric_names = {m["name"] for m in handle.metrics}
    assert {"coherence_score", "turn_taking_score", "scenario_goal_reached"} <= metric_names


@pytest.mark.parametrize(
    "attribution,goal,expected",
    [
        ("none", True, "success"),
        ("none", False, "partial"),
        ("target_agent", False, "failure"),
        ("target_agent", True, "failure"),
        ("inconclusive", False, "inconclusive"),
        ("patient_simulator", False, "partial"),
        ("shared", False, "partial"),
        ("infrastructure", False, "partial"),
    ],
)
def test_evaluation_outcome_mapping(attribution, goal, expected):
    evaluation = SimpleNamespace(
        failure_attribution=attribution, scenario_goal_reached=goal
    )
    assert tvx.evaluation_outcome(evaluation) == expected


def test_completion_metadata():
    metadata = tvx.evaluation_completion_metadata(_sample_evaluation())
    assert metadata["scenario_goal_reached"] is False
    assert metadata["primary_failure_attribution"] == "target_agent"
    assert metadata["target_agent_bug_count"] == 1
    assert metadata["simulator_issue_count"] == 1
    assert metadata["infrastructure_issue_count"] == 1

def test_detects_new_patient_declaration():
    signals = tvx.detect_progress_signals("Hi, I'm a new patient and need an appointment.")
    assert ("patient.declared_new_patient", {}) in signals


def test_detects_phone_identifier_rejection():
    signals = dict(tvx.detect_progress_signals("No, that's not my number."))
    assert signals["identity.identifier_rejected"] == {"identifier_type": "phone"}


def test_detects_identity_rejection_with_correction():
    signals = dict(
        tvx.detect_progress_signals("That's not me — my name is Elena Brooks.")
    )
    assert "identity.identifier_rejected" in signals
    assert "identity.correction" in signals


def test_no_false_positive_on_normal_utterance():
    assert tvx.detect_progress_signals("Tuesday afternoon works great, thank you.") == []
    assert tvx.detect_progress_signals("") == []


def test_callback_urls_include_campaign():
    urls = build_callback_urls(
        "appointment_basic", "good-ai-voice-eval-2026", base_url="https://example.test"
    )
    for url in urls.values():
        assert "scenario_id=appointment_basic" in url
        assert "campaign_id=good-ai-voice-eval-2026" in url
    assert urls["voice"].startswith("https://example.test/voice?")
    assert urls["call_status"].startswith("https://example.test/call-status?")
    assert urls["recording_complete"].startswith("https://example.test/recording-complete?")


def test_callback_urls_without_campaign():
    urls = build_callback_urls("cancel", None, base_url="https://example.test")
    for url in urls.values():
        assert "campaign_id" not in url
