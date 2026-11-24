# CampusPilot — AI-Powered Autonomous College Portal Agent

**An intelligent browser automation agent that understands natural-language instructions and performs tasks on college web portals using Playwright, live DOM analysis, and large language models.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Browser-Playwright-green)](https://playwright.dev/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/Frontend-React-61DAFB)](https://react.dev/)
[![LLM](https://img.shields.io/badge/LLM-Groq-orange)](https://groq.com/)

---

## Overview

Students frequently interact with college portals to register electives, check course availability, download hall tickets, view attendance, apply for leave, and manage academic information.

These processes often involve multiple pages, forms, buttons, and repetitive manual interactions.

**CampusPilot** aims to simplify these tasks through an AI-powered autonomous browser agent. Students describe what they want in plain English, and the agent interprets the request, understands the live webpage, plans the next action, and interacts with the portal through a real browser.

Unlike traditional automation scripts that depend on fixed selectors and predefined workflows, CampusPilot is designed to understand changing webpage structures and adapt its actions based on the current page state.

## Problem Statement

Develop an AI agent that enables students to describe their requirements in natural language and automatically performs appropriate tasks on college web portals.

For example:

- "Register me for an open elective that doesn't conflict with my timetable."
- "Check my attendance percentage for each subject."
- "Download my latest semester hall ticket."
- "Find available elective courses and show me their details."
- "Help me apply for leave."

The agent should understand the request, navigate the portal, interact with relevant elements, verify the result, and request user confirmation before performing consequential actions.

## Key Features

### 1. Natural Language Task Understanding
- Accepts instructions in plain English.
- Interprets user intent and identifies task objectives.
- Breaks complex requests into smaller steps.
- Uses an LLM to plan browser actions.

### 2. Real-Time Browser Automation
- Uses Playwright to control a real Chromium browser.
- Navigates college portal URLs.
- Interacts with forms, buttons, links, dropdowns, and tables.
- Supports dynamic pages and changing content.
- Provides browser execution visibility.

### 3. Intelligent DOM Understanding

CampusPilot analyzes the live webpage rather than relying entirely on predefined selectors.

Its DOM understanding approach can be organized hierarchically:

1. **Page Level:** Understand the current page and its purpose.
2. **Section Level:** Identify forms, tables, navigation menus, and dialogs.
3. **Element Level:** Identify relevant buttons, links, inputs, and options.
4. **Action Level:** Construct and validate the appropriate Playwright action.

It prioritizes accessible element information and user-facing locators to improve reliability.

### 4. AI-Based Decision Making
- Uses Groq-powered LLM inference.
- Converts natural-language goals into structured actions.
- Uses current page information to decide the next step.
- Re-evaluates the page after each action.

### 5. Security and User Approval
- Validates proposed browser actions before execution.
- Separates LLM reasoning from deterministic security checks.
- Requires explicit user confirmation for consequential actions.
- Prevents unauthorized or unapproved submissions.
- Treats webpage content as untrusted input.
- Avoids exposing credentials and sensitive session information to the LLM.

### 6. Task Verification
- Checks the actual page state after browser actions.
- Uses visible page content and other available evidence.
- Distinguishes successful actions from failed or uncertain actions.
- Avoids reporting completion based only on an LLM response.

### 7. Execution Monitoring
- Displays task progress and execution events.
- Supports browser screenshots and execution feedback where configured.
- Maintains action history for tracking and debugging.

---

## System Architecture

```text
              STUDENT
                 |
                 v
       Natural Language Request
                 |
                 v
        REACT FRONTEND
                 |
                 v
          FASTAPI BACKEND
                 |
                 v
         TASK MANAGEMENT
                 |
          +------+------+
          |             |
          v             v
     GROQ LLM       SECURITY LAYER
          |             |
          v             v
    ACTION PLANNING  ACTION VALIDATION
          |             |
          +------+------+
                 |
                 v
       PLAYWRIGHT BROWSER
                 |
                 v
         LIVE COLLEGE PORTAL
                 |
                 v
          LIVE DOM ANALYSIS
                 |
                 v
        RESULT VERIFICATION
                 |
                 v
       USER FEEDBACK / APPROVAL
```

### Agent Execution Cycle

CampusPilot follows an iterative browser-agent workflow:

**Observe → Understand → Plan → Validate → Act → Verify**

1. Observe the current browser page.
2. Extract relevant DOM and accessibility information.
3. Send the task context to the LLM.
4. Receive a structured action proposal.
5. Validate the proposed action through security rules.
6. Request user approval when required.
7. Execute the permitted action using Playwright.
8. Observe the updated page and verify the outcome.
9. Continue until the task is completed, blocked, or requires user input.

---

## Technology Stack

| Component | Technology |
|---|---|
| Frontend | React, JavaScript, Vite |
| Backend | Python, FastAPI |
| Browser Automation | Playwright |
| LLM Provider | Groq API |
| DOM Understanding | Live DOM and Accessibility Information |
| AI Decision Making | LLM-based Action Planning |
| Security | Deterministic Action Validation |
| API Communication | REST API |
| Real-Time Updates | Server-Sent Events (where configured) |
| Testing | Pytest, Playwright |

---

## Project Structure

```text
CampusPilot/
│
├── backend/
│   ├── main.py
│   │
│   ├── config/
│   │   └── settings.py
│   │
│   ├── routes/
│   │   └── automation_routes.py
│   │
│   ├── services/
│   │   ├── llm_service.py
│   │   ├── task_manager.py
│   │   ├── browser_service.py
│   │   ├── security_service.py
│   │   ├── approval_service.py
│   │   └── report_service.py
│   │
│   ├── utils/
│   │   ├── security_rules.py
│   │   └── action_logger.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── Frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── index.html
│
└── README.md
```

*The structure above describes the intended project organization; adjust filenames to match the current repository.*

---

## Installation and Setup

### Prerequisites

- Python 3.10 or later
- Node.js and npm
- Git
- Chromium browser support through Playwright
- Groq API key

### 1. Clone the Repository

```bash
git clone https://github.com/Mohan007N/AI-Hackathon.git

cd AI-Hackathon
```

### 2. Set Up the Backend

```bash
cd backend

python -m venv venv
```

Activate the virtual environment.

**Windows:**

```bash
venv\Scripts\activate
```

**Linux / macOS:**

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Install the Playwright Chromium browser:

```bash
playwright install chromium
```

### 3. Configure Environment Variables

Create a `.env` file inside the backend directory.

```env
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b
GROQ_BASE_URL=https://api.groq.com/openai/v1

BROWSER_HEADLESS=false
```

Use the model and configuration supported by your current Groq account and application settings.

**Important:** Never commit `.env` files, API keys, passwords, or session credentials to GitHub.

### 4. Start the Backend

From the backend directory:

```bash
uvicorn main:app --reload
```

If your application uses a different Python module path, use the corresponding entry point.

### 5. Start the Frontend

Open another terminal:

```bash
cd Frontend

npm install

npm run dev
```

Open the local frontend URL displayed by Vite.

---

## How to Use CampusPilot

1. Open the CampusPilot web application.
2. Enter the college portal URL.
3. Provide the required login credentials securely.
4. Describe the task in natural language.
5. Start the agent.
6. Observe the browser as it navigates the portal.
7. Review proposed actions and approve consequential operations when prompted.
8. Check the final execution result and available verification evidence.

### Example Task

```text
Find an open elective that does not conflict
with my existing timetable.

Show me the available options and their details.

Do not register until I confirm.
```

The agent should inspect the timetable, identify suitable electives, present the available options, and wait for the user's decision before registration.

---

## Security and Responsible Automation

CampusPilot is designed around the principle that **AI-generated actions should never be trusted without validation**.

Security considerations include:

- User credentials should remain outside LLM prompts and application logs.
- Browser navigation should be restricted to validated URLs and permitted destinations.
- Webpage instructions must be treated as untrusted content.
- Every proposed action should pass deterministic validation.
- Irreversible actions must require explicit and fresh user approval.
- The agent must not bypass security challenges or access controls.
- Failed or uncertain actions must not be reported as successful.
- Sensitive information should be excluded from screenshots, logs, and reports wherever possible.

**Human-in-the-loop principle:** The agent can assist with decisions and browser interactions, but the user retains control over consequential actions.

---

## Testing and Validation

Testing should cover multiple levels:

| Testing Level | Purpose |
|---|---|
| Unit Testing | Validate individual services and security rules |
| API Testing | Test backend endpoints and task lifecycle |
| Browser Testing | Verify actual Playwright interactions |
| DOM Testing | Test element identification and dynamic page changes |
| Security Testing | Test prompt injection, unauthorized actions, and approval enforcement |
| End-to-End Testing | Validate the complete frontend-to-browser workflow |

A successful unit test does not automatically prove that live browser automation works. Real-browser and end-to-end testing are necessary before claiming full functionality.

---

## Future Enhancements

- Multi-step task planning and execution.
- Better hierarchical DOM and accessibility-tree understanding.
- Improved support for complex forms and dynamic portals.
- Multi-goal task execution.
- More reliable evidence-based verification.
- Persistent task recovery and approval workflows.
- Enhanced prompt-injection and browser-agent security.
- Support for additional college portal systems.
- Improved execution monitoring and reporting.

---

## Project Vision

CampusPilot aims to transform traditional college portal interactions into a simpler, conversational experience.

Instead of manually navigating multiple pages and filling out repetitive forms, students can explain what they need while an AI agent assists with the actual browser workflow.

**Our goal is not just to automate clicks — it is to build a reliable, secure, and user-controlled autonomous web agent for academic workflows.**

---

## Contributors

Developed as part of an AI Hackathon project.

**Repository:** [AI-Hackathon](https://github.com/Mohan007N/AI-Hackathon)

---

## Acknowledgements

- [Playwright](https://playwright.dev/) — Browser automation
- [FastAPI](https://fastapi.tiangolo.com/) — Backend API framework
- [React](https://react.dev/) — Frontend development
- [Groq](https://groq.com/) — LLM inference

---

**CampusPilot — Your College Portal, One Conversation Away.**
