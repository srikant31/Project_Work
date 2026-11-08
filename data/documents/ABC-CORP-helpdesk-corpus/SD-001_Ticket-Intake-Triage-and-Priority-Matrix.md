# SD-001 — Ticket Intake, Triage, and Priority Matrix

## Document control

| Field | Value |
|---|---|
| Document ID | SD-001 |
| Title | Ticket Intake, Triage, and Priority Matrix |
| Version | 4.0 |
| Owner | Service Desk Manager |
| Approver | Director, IT Operations |
| Effective date | March 14, 2025 |
| Review cycle | Annual |
| Audience | Service Desk agents, IT Operations |
| Classification | Internal |

## Priority matrix

| Priority | Definition | First response | Resolution target |
|---|---|---|---|
| P1 | Business-critical system down for many users, security incident active, executive unable to work | 15 min | 4 hours |
| P2 | Major function impaired, multiple users affected, individual unable to work | 30 min | 8 business hours |
| P3 | Single user impaired, workaround exists, or standard service request | 4 business hours | 3 business days |
| P4 | Request for information, cosmetic issue, change request | 1 business day | 10 business days |

## Intake channels

| Channel | Route to |
|---|---|
| Self-service portal | Queue by category |
| Email to helpdesk mailbox | Queue by category |
| Phone | Agent creates ticket during call |
| Chat | Agent creates ticket during chat |
| Walk-up | Agent creates ticket at desk |
| Monitoring alert | Route to on-call engineer |

Every interaction creates a ticket. No exceptions. Phone calls that resolve in under 5 minutes are still ticketed with a resolution note — the record matters for trend analysis and for the next person who calls about the same thing.

## Triage rules

Priority is set by impact and urgency, not by the requester's tone. A user demanding P1 for a password reset does not change the matrix.

**Impact** — how many users are affected, and how badly.
**Urgency** — how quickly the impact will worsen if not addressed.

Set priority using:

| | High urgency | Medium urgency | Low urgency |
|---|---|---|---|
| High impact | P1 | P2 | P3 |
| Medium impact | P2 | P3 | P4 |
| Low impact | P3 | P4 | P4 |

## Priority escalators

Raise priority by one level when:

- The requester is an executive or their executive assistant
- A customer-facing function is affected
- A regulatory or contractual deadline is at risk
- The ticket is approaching its resolution target without progress

Raise to P1 regardless of matrix when:

- An active security incident is suspected
- A production system is down for an entire team or floor
- Data loss is suspected

## Priority de-escalators

Lower priority by one level when:

- A documented workaround exists
- The requester is not blocked
- The request is a preference rather than a need
- The request is a change rather than a fix

Never de-escalate without documenting the reason in the ticket.

## Routing

| Category | Queue |
|---|---|
| Access and credentials | Identity team |
| Hardware | Asset team |
| Software | Application support |
| Network and VPN | Network operations |
| Email and calendar | Collaboration team |
| HR systems | HRIS support |
| Facilities | Facilities |
| Finance systems | Finance systems support |
| Security incidents | Security operations (immediate) |

Routing is a Service Desk responsibility. Do not route to a queue that cannot resolve the ticket — if the category is unclear, escalate to the shift lead rather than guessing.

## Ticket content requirements

Every ticket must have:

- Requester name and employee ID
- Contact method for follow-up
- Description of the issue in the requester's own words
- Category
- Priority
- Assignment group
- Any attachments (screenshots, error messages, receipts)

Tickets missing any of these are returned by the queue owner. Returned tickets count against the agent's quality metrics.

## Bulk and repeated tickets

When more than 10 tickets are created for the same issue within one hour, the shift lead consolidates them into a single major incident (SD-002) and links the individual tickets to the parent.

## Reopened tickets

Reopened tickets retain their original priority for 5 business days. After 5 business days, priority is reassessed.

## After-hours and weekends

After-hours tickets are triaged by the on-call agent. P1 and P2 are worked. P3 and P4 are queued for the next business day unless the requester is a designated on-call employee.

## Related documents

- SD-002 Escalation, Handoff, and Major Incident Communication
- SD-003 Service Catalog and Request Fulfillment
- IT-002 Password, MFA, and Credential Reset Procedure
- SE-004 Incident Reporting and Security Escalation

## Revision history

| Version | Date | Change |
|---|---|---|
| 3.0 | 07/19/2023 | Priority matrix revised |
| 3.8 | 03-14-2024 | Escalator and de-escalator rules added |
| 4.0 | March 14, 2025 | Bulk ticket consolidation added; after-hours section clarified |
