# Final Bug Report — Pretty Good AI Voice Agent Evaluation

I used the automated per-call evaluation as a first pass, then reviewed the selected recordings, transcripts, timestamps, and causal sequence before deciding which findings belonged in this final report. I excluded findings that depended on uncertain diarization or post-call transcription errors. The three findings below are the ones I consider both useful and supportable from the selected evidence.

## 1. Rejected caller-ID identity can be reintroduced after explicit correction

- **Severity:** High
- **Category:** Identity state / data integrity
- **Primary evidence call:** `CA217fbbb73a8e5de23c5d0f9ce3ee93f9` (`identity_isolation`)
- **Evidence timestamps:** `01:13.94–01:21.14` and `01:44.92–01:56.67`
- **Confidence:** High

### What I observed

The caller says she is a new patient, has never called before, cannot confirm a phone number on file, and asks the receptionist to use her name and date of birth instead. The agent accepts that clarification. Later in the same interaction, it says, **“I have your phone number as 878-234-7232 ... Let me continue with your information.”** The caller then has to reject the number again.

I also observed the same originating number being initially associated with “Jordan” in calls using different synthetic patient identities. I do not treat the initial caller-ID association alone as proof of an internal implementation defect because the test environment may intentionally map the number to a fixture. The stronger failure is what happens after explicit correction: identity data that the caller rejected is still reintroduced into the active workflow.

### Why I consider this important

In a medical receptionist workflow, an identifier that has been explicitly rejected should not remain authoritative. Reusing it creates a risk of wrong-patient association, incorrect routing, or attaching later actions to the wrong identity context.

### Expected behavior

Once the caller rejects a caller-ID-derived identifier or establishes that they are a new patient, the agent should discard that identifier from the active identity state and continue only with information the caller has verified.

---

## 2. New-patient scheduling can fall back into an existing-record lookup instead of progressing the appointment request

- **Severity:** High
- **Category:** Workflow / task completion
- **Evidence call:** `CA66a4b6d60e2b8258d149b53e8ad8e103` (`appointment_basic`)
- **Evidence timestamps:** `00:13.42–00:20.67`, `01:20.16–01:24.11`, and `01:44.67–01:52.76`
- **Confidence:** High

### What I observed

The caller opens by saying she is a **new patient** and wants a routine appointment, with Tuesday afternoon as her preference. The receptionist initially associates the calling number with “Jordan.” The caller corrects this and repeats that she is new and does not have a phone number on file with the practice.

After the identity exchange, the agent says, **“One moment while I look up your information,”** and then transfers the call. No appointment availability is discussed, and the interaction does not reach a concrete new-patient scheduling step.

### Why I consider this important

The agent appears to understand the words “new patient,” but the workflow does not reliably transition into new-patient scheduling. A common front-desk request can therefore fall back into a record-lookup path that cannot succeed for the caller and does not advance the stated goal.

### Expected behavior

Once new-patient status is established, the agent should move into the appropriate intake/scheduling flow, collect only the information required for that path, and either offer appointment availability or clearly explain the next scheduling action.

---

## 3. Identity verification can become repetitive after the caller has already provided usable information

- **Severity:** Medium
- **Category:** Conversation efficiency / identity verification
- **Evidence calls:**
  - `CAf36ff057a3db028c6f7a6aaefb59e2ee` (`cancel`)
  - `CA06b6e29d141d2ecc8d903c4b607a5195` (`refill`)
- **Representative timestamps:**
  - Cancel: `00:31.92–00:35.92`, `01:04.95–01:16.30`, `01:17.94–01:32.59`
  - Refill: `00:35.71–00:39.01`, `01:08.27–01:17.12`, `01:25.04–01:48.72`
- **Confidence:** Medium-high

### What I observed

In both calls, the patient explains that they cannot confirm the phone number on file and provides name and date of birth instead. The agent later returns to the phone-number option and/or repeats spelling and date-of-birth verification that has already been provided. In the refill call, a phone number is later asserted even after the caller has asked to continue with name and date of birth.

### Why I consider this important

The repetition makes the calls longer, increases frustration, and suggests that verified state is not always being carried forward efficiently between turns. It also increases the chance that an unavailable or rejected identifier will be brought back into the conversation.

### Expected behavior

The agent should preserve verified identity information across turns, stop asking for an unavailable identifier once a supported fallback has been chosen, and repeat previously confirmed fields only when there is a specific reason to do so.

---

## Calls where the target behavior was appropriate

I intentionally kept successful calls in the evaluation set because I wanted the harness to distinguish a real defect from a call that simply did not produce one.

- **Location:** the agent gave a clear and internally consistent response to the location request.
- **Insurance:** the agent avoided making an unsupported coverage guarantee and offered an appropriate next step.
- **Office hours/weekend:** the agent handled the weekend-hours question coherently and provided weekday alternatives.
- **Ambiguous request:** the agent asked for clarification rather than guessing what the caller meant.
- **Cancellation:** when the record could not be located, the agent did not falsely claim that the appointment had been cancelled.

This mix of positive and negative outcomes is important to me because the goal of the project is to evaluate the target system, not to force every call into a bug report.
