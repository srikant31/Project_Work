# IT-005 — VPN and Remote Access Quick Reference

## Document control

| Field | Value |
|---|---|
| Document ID | IT-005 |
| Title | VPN and Remote Access Quick Reference |
| Version | 1.8 |
| Owner | Network Operations |
| Approver | Director, IT Operations |
| Effective date | 03-14-2025 |
| Review cycle | Annual |
| Audience | All remote workers |
| Classification | Internal |

## Connecting

| Step | Action |
|---|---|
| 1 | Confirm your device is on and connected to the internet |
| 2 | Open the VPN client from the system tray |
| 3 | Select the profile ABC-CORP-Primary |
| 4 | Authenticate with your username, password, and MFA prompt |
| 5 | Wait for the tray icon to show connected (green) |

### Connecting on Windows

- Click the VPN client icon in the system tray. If the icon is not visible, expand the tray overflow.
- If the client has not been used in 30 days, it may need an update on first launch. Accept the update and wait for it to complete before connecting.
- Enter your ABC CORP username in the format domain\username.
- Complete the MFA prompt with your enrolled method.

### Connecting on macOS

- Open the VPN client from the menu bar or the Applications folder.
- Approve any Keychain access prompt using your device login password — not your ABC CORP password.
- Enter your ABC CORP username.
- Complete the MFA prompt with your enrolled method.

### Connecting on mobile

Mobile devices connect through the ABC CORP mobile app. Open the app, select VPN, and authenticate. Mobile VPN is required only for access to internal email and file shares; public cloud applications do not require mobile VPN.

## Session limits

| Setting | Value |
|---|---|
| Session timeout | 8 hours |
| Idle timeout | 30 minutes |
| Reconnect grace | 5 minutes |
| Concurrent sessions per user | 2 |

Sessions that reach the 8-hour limit are terminated. Save your work before the limit or you may lose unsaved changes. The client shows a warning at 15 minutes remaining and again at 5 minutes remaining.

## Common errors

| Error | Meaning | Fix |
|---|---|---|
| AUTH-401 | Wrong credentials | Re-enter password; if repeated, reset via IT-002 |
| AUTH-403 | Account locked | Wait 30 minutes or call Service Desk |
| NET-102 | Cannot reach gateway | Check internet; retry; try alternate profile |
| NET-205 | Certificate expired | Contact Service Desk |
| MFA-011 | MFA not enrolled | Contact Service Desk |
| NET-310 | Concurrent session limit | Disconnect another device |
| TUN-044 | Local firewall blocking tunnel | Restart device; if persistent, contact Service Desk |

## Split tunnel

Split tunnel is enabled for approved cloud applications. Traffic to ABC CORP internal systems always routes through the VPN. Traffic to the public internet routes direct. Do not attempt to change tunnel settings.

If you are on a network that blocks VPN connectivity — for example, certain hotel and airline networks — the client attempts a TCP fallback. If the fallback fails, switch to a mobile hotspot and retry.

## When to call the Service Desk

Call if:

- You see an error not listed above
- You have been disconnected repeatedly in a 24-hour period
- You cannot authenticate after two password attempts
- You need access to a system that is not reachable after connecting
- You see the NET-310 concurrent session error and cannot identify the other device

Do not call for:

- Slow internet speed on non-ABC CORP destinations
- Requests to change tunnel configuration
- Issues that resolve after disconnecting and reconnecting once
- Requests to extend session timeout for a specific task

## Troubleshooting ladder

Work down the ladder before calling. Each step takes under two minutes.

1. Disconnect and reconnect once.
2. Confirm your internet works without the VPN — open a public website in a browser tab.
3. Restart the VPN client from the tray menu.
4. Restart your device.
5. Try the alternate profile ABC-CORP-Secondary.
6. If all of the above fail, call the Service Desk and report which steps you completed.

## Related documents

- IT-002 Password, MFA, and Credential Reset Procedure
- SD-001 Ticket Intake, Triage, and Priority Matrix
- SD-003 Service Catalog and Request Fulfillment

## Revision history

| Version | Date | Change |
|---|---|---|
| 1.6 | 03-14-2024 | Added split tunnel note |
| 1.7 | 09/12/2024 | Added macOS and mobile sections |
| 1.8 | March 14, 2025 | Error table expanded; troubleshooting ladder added |
