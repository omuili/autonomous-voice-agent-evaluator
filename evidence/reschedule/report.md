# Call Report — Reschedule an existing appointment

- Scenario ID: `reschedule`
- Call SID: `CA762a66c59e3d2df15d7a620bc8ea5071`
- Recording SID: `RE64186e9ddc7c2d5ee4c669c215f10a9f`
- Coherent conversation: `True`
- Coherence score: `5/5`
- Turn-taking score: `5/5`
- Scenario goal reached: `False`
- Primary failure attribution: `none`

## Summary

The receptionist behaved appropriately: it verified identity, checked alternative lookup options, and offered transfer to patient support. The call did not reach a completed reschedule, but there is no material target-agent failure observable in the transcript. The main issue was simulator behavior at the end of the call.

## Primary failure reason

The call was handled coherently and the receptionist verified identity and offered transfer to patient support, but there was no observable evidence of an appointment being rescheduled or of a change confirmation. The scenario’s hidden objective about distinguishing rescheduling from duplicate creation was not explicitly tested in a way that produced a live failure.

## Simulator quality notes

- The patient simulator revealed the test harness at the end by saying 'you've reached the pretty good AI test line,' which is unnatural and can contaminate the call flow.
- The simulator also ended the interaction with a goodbye after the transfer, limiting the chance to complete the rescheduling workflow.

## Simulator issues

### S1. Simulator revealed test line after transfer

- Timestamp: `01:40.32`
- Confidence: `0.99`

**Evidence:** C said: 'you've reached the pretty good AI test line.'

**Impact:** Breaks realism and can distract the practice side from the intended rescheduling task.


## Infrastructure / evidence-quality issues

No material infrastructure or evidence-quality issues identified.

## Target-agent strengths

- Asked for full name and date of birth before proceeding.
- Confirmed the name and DOB back to the caller for verification.
- Offered an appropriate fallback by connecting to patient support when the record could not be found.

## Target-agent bugs

No material target-agent bugs identified.