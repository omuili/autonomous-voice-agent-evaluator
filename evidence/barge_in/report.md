# Call Report — Natural interruption / barge-in

- Scenario ID: `barge_in`
- Call SID: `CA94d4cf3cf00a38b3e0cfdf46648b0735`
- Recording SID: `REb19aa2906d0dc01df871d151f549237a`
- Coherent conversation: `True`
- Coherence score: `4/5`
- Turn-taking score: `4/5`
- Scenario goal reached: `False`
- Primary failure attribution: `patient_simulator`

## Summary

The target agent handled the barge-in smoothly and preserved Casey Williams' corrected information. The scenario failed because the patient simulator broke role-play after the transfer by announcing the test line and saying goodbye, which is a simulator issue rather than a receptionist bug.

## Primary failure reason

The call derailed after the transfer because the patient simulator revealed the test line instead of continuing the scheduling conversation, preventing completion of the scenario.

## Simulator quality notes

- The simulator abruptly disclosed the test line at 01:51.11, which is outside the scripted patient persona and directly breaks the scheduling scenario.
- The final exchange is repeated goodbye/test-line chatter rather than a normal patient response, indicating the harness did not maintain the intended caller role.

## Simulator issues

### S1. Simulator revealed test line after transfer instead of continuing as patient

- Timestamp: `01:51.11`
- Confidence: `0.99`

**Evidence:** [01:51.11 - 01:51.46] C: Hello, you've reached the Pretty Good ai test line. [01:54.61 - 01:55.01] C: Good bye.

**Impact:** Prevented the scheduling conversation from completing and caused the scenario to fail for reasons unrelated to the target agent.


## Infrastructure / evidence-quality issues

No material infrastructure or evidence-quality issues identified.

## Target-agent strengths

- Acknowledged the interruption and continued the identity verification workflow without losing the corrected name or DOB.
- Repeated back the corrected name and date of birth accurately before proceeding.
- Handled the phone-number uncertainty appropriately by offering an alternate verification path.
- Transferred the caller to patient support when the record could not be located, rather than becoming stuck or contradicting the caller.

## Target-agent bugs

No material target-agent bugs identified.