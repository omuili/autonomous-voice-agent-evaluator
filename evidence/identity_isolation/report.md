# Call Report — New-patient caller identity isolation

- Scenario ID: `identity_isolation`
- Call SID: `CA217fbbb73a8e5de23c5d0f9ce3ee93f9`
- Recording SID: `REe70eda892aba6458700048fe64d0905f`
- Coherent conversation: `True`
- Coherence score: `4/5`
- Turn-taking score: `3/5`
- Scenario goal reached: `False`
- Primary failure attribution: `target_agent`

## Summary

The call mostly followed a new-patient intake flow, but the target agent failed the identity-isolation test by using a phone-number record after the caller explicitly said they were a new patient and could not confirm it. It then compounded the issue by misnaming the caller after the correction.

## Primary failure reason

The receptionist initially handled the caller as a new patient, but later incorrectly associated the caller with a stale phone-number record and then continued acting on that mistaken identity even after the caller explicitly rejected it.

## Simulator quality notes

- The simulator’s authoritative utterance at 00:48-00:57 spells the name as E-L-E-N-A-B-R-O-O-K-S without a pause between first and last name, which is slightly unnatural but not a failure.

## Simulator issues

No material simulator-induced issues identified.

## Infrastructure / evidence-quality issues

No material infrastructure or evidence-quality issues identified.

## Target-agent strengths

- Asked for full name and date of birth before proceeding.
- Acknowledged the caller’s name and DOB and attempted a record lookup workflow.
- Offered to connect the caller to patient support for a new-patient appointment.

## Target-agent bugs

### 1. Relied on a phone-number record after the caller explicitly said they were a new patient and could not confirm it

- Severity: **High**
- Category: `Identity isolation / new-patient intake`
- Timestamp: `00:44.92`
- Confidence: `0.95`

**Evidence:** At 00:44.92-00:53.72, B says, "I have your phone number as 878-234-7232... Let me continue with your information." This happens after C already said at 01:13.94-01:21.14, "I can't confirm a phone number on file. I'm a new patient and I've never called before, so please use my name and date of birth instead."

**Why it matters:** The scenario is specifically testing whether the practice incorrectly reuses an identity tied to the calling number. Continuing from a phone-number lookup after the caller rejected that identity can misroute a new patient and violates identity isolation.

**Expected behavior:** After the caller rejects the phone-number lookup and states they are a new patient, the receptionist should reset to name/DOB-based intake and avoid asserting any record or identity from caller ID.

### 2. Misaddressed the caller with an incorrect name after the caller corrected the phone-number assumption

- Severity: **Medium**
- Category: `Identity handling / data integrity`
- Timestamp: `02:00.48`
- Confidence: `0.88`

**Evidence:** After C says, "That phone number is not me. I'm a new patient." at 01:58.58-02:01.08, B responds, "Thank you, Alada Brooks, date of birth February 9, 1987."

**Why it matters:** Using a different name after the caller explicitly corrected the identity assumption shows the receptionist did not properly reset state and risks creating or attaching the wrong chart.

**Expected behavior:** The receptionist should acknowledge the correction, discard the stale identity, and continue with the caller as Elena Brooks based on the provided name and DOB.
