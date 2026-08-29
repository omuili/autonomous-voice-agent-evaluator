# Call Report — Weekend office-hours constraint

- Scenario ID: `office_hours_weekend`
- Call SID: `CAeffa44ab73d02cc7339d89d451a0599c`
- Recording SID: `RE6abf013de61864a1d3aab2b884bf7cf6`
- Coherent conversation: `True`
- Coherence score: `4/5`
- Turn-taking score: `4/5`
- Scenario goal reached: `True`
- Primary failure attribution: `none`

## Summary

The call succeeded. The receptionist appropriately refused the weekend appointment request, offered weekday alternatives, and handled verification/transfer without a material issue. The only notable problem was a post-call transcript wording mismatch from the simulator side, which is an evidence-quality issue rather than a live call failure.

## Primary failure reason

The target agent correctly stated that the office is closed on weekends, offered weekday alternatives, and then completed identity verification and transfer assistance without a material conversational failure.

## Simulator quality notes

- One simulator utterance was slightly mistranscribed as 'a weekend yearning would be helpful' at 00:35.31, but the authoritative simulator output indicates the intended response was 'Yes, a weekday morning would be helpful.' This did not affect the live conversation.

## Simulator issues

No material simulator-induced issues identified.

## Infrastructure / evidence-quality issues

### I1. Post-call transcript contains a simulator wording mismatch

- Timestamp: `00:35.31`
- Confidence: `0.96`

**Evidence:** Authoritative simulator output says 'Yes, a weekday morning would be helpful. Could you check the next available morning appointment?' while the diarized transcript shows 'Yes, a weekend yearning would be helpful.'

**Impact:** Evidence quality is slightly degraded, but the live call remained understandable and the target agent still responded appropriately.


## Target-agent strengths

- Immediately rejected the requested Sunday slot by stating the practice is not open on weekends.
- Offered a valid alternative: next available weekday appointment and morning options.
- Used a reasonable identity-verification flow and provided transfer assistance when unable to find the record.

## Target-agent bugs

No material target-agent bugs identified.