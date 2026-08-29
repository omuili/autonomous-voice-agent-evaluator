# Call Report — Simple appointment scheduling

- Scenario ID: `appointment_basic`
- Call SID: `CA66a4b6d60e2b8258d149b53e8ad8e103`
- Recording SID: `REa9c64159b5ce3b4e2c51068a98128a86`
- Coherent conversation: `True`
- Coherence score: `4/5`
- Turn-taking score: `4/5`
- Scenario goal reached: `False`
- Primary failure attribution: `target_agent`

## Summary

The call was coherent, but the receptionist did not meaningfully advance new-patient scheduling. It correctly clarified identity details, yet then pivoted to a record lookup and transferred the caller away instead of discussing appointment availability or next steps. The name-spelling discrepancy in the transcript is an infrastructure/evidence issue, not a live-call failure.

## Primary failure reason

The receptionist failed to progress new-patient appointment scheduling and instead performed a record lookup/transfer flow, then transferred the caller away without collecting scheduling details or offering availability.

## Simulator quality notes

- The simulator’s spoken name spelling at 00:57.76-01:03.26 is transcribed incorrectly in the diarized record, but the authoritative simulator output says it spelled M-A-Y-A, T-H-O-M-P-S-O-N. This is an evidence-quality/transcription issue, not a caller behavior issue.

## Simulator issues

No material simulator-induced issues identified.

## Infrastructure / evidence-quality issues

### I1. Post-call transcript misrendered the caller's spelled name

- Timestamp: `00:57.76 - 01:03.26`
- Confidence: `0.98`

**Evidence:** Authoritative simulator output states the caller spelled the name as M-A-Y-A, T-H-O-M-P-S-O-N, but the diarized transcript renders it as M-A-Y-A-T-H-O-N-P-S-O-N.

**Impact:** This creates a misleading record of what the caller said, but it did not affect the live call because transcription occurs after the conversation.


## Target-agent strengths

- The receptionist appropriately asked for date of birth and confirmed the caller's name when handling the record lookup.
- The receptionist correctly acknowledged the caller's clarification that they were a new patient and had no phone number on file.

## Target-agent bugs

### 1. Routed a new patient into a record lookup/transfer instead of scheduling flow

- Severity: **High**
- Category: `workflow`
- Timestamp: `01:44.67 - 01:52.76`
- Confidence: `0.95`

**Evidence:** After the caller identified as a new patient seeking a routine appointment, B said 'One moment while I look up your information,' then 'Transferring you now,' and the call was handed off without any appointment availability being discussed or a scheduling next step being offered.

**Why it matters:** This blocks the core task: a new patient wanted to schedule or at least meaningfully progress toward scheduling, but the receptionist did not take the call toward an appointment.

**Expected behavior:** Recognize the new-patient scheduling context, gather the necessary intake details, explain the appropriate new-patient process, and either schedule, offer availability, or clearly explain the next step without unnecessary transfer away from the interaction.
