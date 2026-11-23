# CampusPilot — Multi-site Browser Automation Dashboard

CampusPilot is a **website-agnostic task dashboard**. It is not a college website and does not recreate any target site. A signed-in user enters a website URL, describes the required task in natural language, and can optionally provide one-time website login details. A connected browser automation backend can then interact with that site.

## Run in VS Code

```powershell
npm.cmd install
npm.cmd run dev
```

Open the localhost URL printed by Vite (normally `http://localhost:5173`). In PowerShell, use `npm.cmd` if script execution policy blocks `npm.ps1`.

## What's in this version

- Sign-up and sign-in screens.
- Local demo accounts, with passwords stored as salted PBKDF2 verifiers rather than plain text.
- Past Records page: shows the website URL, user instruction, creation time, and status. It never shows website usernames or passwords.
- Dedicated Approval Queue: sensitive tasks are stored until the user explicitly approves or rejects them. The queue displays the website link and requested instruction, never credentials.
- A minimal task console with only the website link and natural-language instruction as its main inputs.
- Optional one-time website sign-in details in a separate modal. These details remain in React memory only and are cleared after the request is handed off, blocked, or cancelled.
- Site-agnostic task classification and feedback for vague or unsupported requests.
- Explicit approval for potentially consequential actions, including payments, purchases, bookings, form filling/submission, uploads, account changes, deletions, and cancellations.
- A curated restricted-domain check for known adult-content and piracy/cracked-software distribution sites, including the examples discussed (Pornhub, FitGirl Repacks, and DODI Repacks).
- Responsive white-theme interface for desktop and mobile.

## Site safety limitations

The restricted-domain list is **curated and incomplete**. It is a basic frontend guardrail, not a malware scanner, content filter, or live domain-reputation service. It cannot identify every phishing page, newly created malicious domain, mirror site, redirect, or harmful download. A production backend must independently validate final redirect destinations and use a maintained threat-intelligence / safe-browsing service before opening a site. The backend must enforce the policy; it must not trust the frontend's result.

The frontend rejects embedded URL credentials and requires HTTPS for public sites. `http://localhost` and `http://127.0.0.1` remain allowed for development.

## Authentication and local data

This is a **frontend prototype**. Sign-up and sign-in are local to the current browser and are not connected to a real identity provider. Passwords are not saved in plain text; the browser stores a salted PBKDF2 verifier so a local demo account can be used again. This is not a substitute for production authentication, server-side rate limiting, account recovery, or secure session management.

Task records and pending approval requests are stored separately in this browser's local storage and are scoped by the signed-in email address. They do not contain website sign-in credentials. Clearing browser storage removes these local demo accounts and records.

## Frontend vs. real automation

A static React page cannot securely control arbitrary websites by itself. Real execution requires a trusted backend service with Playwright or Selenium, isolated browser sessions, secure credential/session handling, destination checks after redirects, action permissions, and server-side approval enforcement.

Without a backend URL configured, CampusPilot validates and saves the task as `Not executed`. It does not open the target website, scrape it, or claim that a task succeeded.

## Connect a backend later

Create `.env` in the project root:

```env
VITE_AUTOMATION_API_URL=https://your-automation-api.example.com
```

Restart Vite after editing `.env`. The dashboard sends requests to:

`POST {VITE_AUTOMATION_API_URL}/api/automation/execute`

Example request body:

```json
{
  "portalUrl": "https://www.example.com/account",
  "url": "https://www.example.com/account",
  "username": "one-time-user-name",
  "password": "one-time-secret",
  "instruction": "Find my latest invoice and download it",
  "prompt": "Find my latest invoice and download it",
  "userApproved": false,
  "source": "campuspilot-dashboard"
}
```

The credentials in this payload are sent only when an API endpoint is explicitly configured, and only for the current request. The backend must avoid logging raw credentials and should convert them to a short-lived secure browser session as early as possible.

Expected response shape:

```json
{
  "status": "completed | needs_approval | invalid | failed",
  "message": "Task result or explanation",
  "taskId": "server-task-id",
  "steps": ["Website opened", "Relevant page located", "Result retrieved"]
}
```

The backend must independently validate every instruction, website destination, redirect, permission, and approval state. Never trust the frontend's classifier or `userApproved` flag as a security boundary. Irreversible actions should require a server-enforced approval token or a second confirmation step.

## Project structure

- `src/App.jsx` — authentication, task console, site safety checks, approval queue, task records, and API handoff.
- `src/styles.css` — responsive white-theme interface.
- `src/main.jsx` — React entry point.


## Approval queue behavior

Instructions classified as consequential are not immediately sent to the automation runtime. CampusPilot creates a task record with `Needs approval` status and adds a separate item to the Approval Queue. The user can approve or reject that item from the Approvals navigation section. Rejection closes the request without browser interaction. Approval records the decision and, if an API endpoint is configured, submits a new execution request with `userApproved: true`. The backend must still enforce its own authorization, destination checks, and irreversible-action safeguards. An approval click is not itself proof of execution.

## Visual design update

The interface uses a restrained icon set, a neutral link mark, darker high-contrast text, larger readable typography, and more consistent text sizing. It avoids oversized decorative product logos and low-contrast microcopy.
