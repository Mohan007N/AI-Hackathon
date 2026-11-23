"""
Local controlled test portal for Issue 9 end-to-end integration testing.
Serves a realistic student portal with:
- Login authentication
- Attendance table
- Real downloadable PDF Hall Ticket
- Elective course registration form
"""

from __future__ import annotations

import io
from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/test-portal", tags=["Local Test Portal"])

# Minimal valid PDF binary bytes for real file download testing
_SAMPLE_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 84>>stream\n"
    b"BT /F1 18 Tf 50 720 Td (UNIVERSITY EXAMINATION HALL TICKET - SEMESTER 2026) Tj ET\n"
    b"endstream\n"
    b"endobj\n"
    b"xref\n"
    b"0 5\n"
    b"0000000000 65535 f\n"
    b"0000000009 00000 n\n"
    b"0000000052 00000 n\n"
    b"0000000101 00000 n\n"
    b"0000000186 00000 n\n"
    b"trailer<</Size 5/Root 1 0 R>>\n"
    b"startxref\n"
    b"320\n"
    b"%%EOF\n"
)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html>
<head>
    <title>CampusPortal — Student Login</title>
    <style>
        body { font-family: sans-serif; background: #f0f2f5; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); width: 340px; }
        h2 { margin-top: 0; color: #1e293b; }
        input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { width: 100%; padding: 10px; background: #4338ca; color: white; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
        button:hover { background: #3730a3; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Student Portal Sign In</h2>
        <form id="login-form" method="POST" action="/test-portal/login">
            <label for="username">Student ID / Username</label>
            <input id="username" name="username" type="text" placeholder="Enter student ID or username" required value="student101" />
            <label for="password">Password</label>
            <input id="password" name="password" type="password" placeholder="Enter password" required value="demo123" />
            <button id="login-submit" type="submit">Sign In to Portal</button>
        </form>
    </div>
</body>
</html>""",
        status_code=200,
    )


@router.post("/login")
async def process_login(username: str = Form(default=""), password: str = Form(default="")):
    return RedirectResponse(url="/test-portal/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html>
<head>
    <title>CampusPortal — Student Dashboard</title>
    <style>
        body { font-family: sans-serif; background: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }
        .container { max-width: 800px; margin: 0 auto; }
        .card { background: white; padding: 24px; border-radius: 8px; border: 1px solid #e2e8f0; margin-bottom: 20px; }
        h1, h2 { color: #0f172a; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { border: 1px solid #e2e8f0; padding: 10px; text-align: left; }
        th { background: #f1f5f9; }
        .btn { display: inline-block; padding: 10px 18px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px; font-weight: bold; cursor: pointer; border: none; }
        .btn:hover { background: #1d4ed8; }
        .btn-success { background: #059669; }
        .btn-success:hover { background: #047857; }
        input, select { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 4px; margin-right: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Welcome, Student (ID: 2026-CS-419)</h1>

        <!-- Attendance Section -->
        <div class="card" id="attendance-section">
            <h2>Semester Course Attendance</h2>
            <table>
                <thead>
                    <tr><th>Course Code</th><th>Course Title</th><th>Attendance</th><th>Eligibility</th></tr>
                </thead>
                <tbody>
                    <tr><td>CS401</td><td>Operating Systems</td><td>91%</td><td>Eligible</td></tr>
                    <tr><td>CS402</td><td>Database Management Systems</td><td>86%</td><td>Eligible</td></tr>
                    <tr><td>CS403</td><td>Machine Learning</td><td>88%</td><td>Eligible</td></tr>
                    <tr><td>CS404</td><td>Web Technologies</td><td>94%</td><td>Eligible</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Hall Ticket Download Section -->
        <div class="card" id="hallticket-section">
            <h2>Examination Hall Ticket</h2>
            <p>Your semester exam hall ticket is available for download.</p>
            <a id="download-hall-ticket" class="btn" href="/test-portal/download/hall_ticket.pdf" download="hall_ticket.pdf">
                Download Hall Ticket (PDF)
            </a>
        </div>

        <!-- Elective Registration Section -->
        <div class="card" id="elective-section">
            <h2>Open Elective Registration</h2>
            <p>Enroll in an open elective for next semester:</p>
            <form id="elective-form" method="POST" action="/test-portal/register">
                <label for="course-code">Course:</label>
                <select id="course-select" name="course_code">
                    <option value="OE162">OE162 — Digital Marketing</option>
                    <option value="OE163">OE163 — Financial Analytics</option>
                    <option value="OE164">OE164 — Environmental Science</option>
                </select>
                <button id="enroll-btn" class="btn btn-success" type="submit">Enroll Course</button>
            </form>
        </div>
    </div>
</body>
</html>""",
        status_code=200,
    )


@router.get("/download/hall_ticket.pdf")
async def download_hall_ticket():
    """Serves an actual binary PDF file with nonzero size for real download verification."""
    return Response(
        content=_SAMPLE_PDF_BYTES,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="hall_ticket.pdf"',
            "Content-Length": str(len(_SAMPLE_PDF_BYTES)),
        },
    )


@router.post("/register", response_class=HTMLResponse)
async def register_elective(course_code: str = Form(default="OE162")):
    """Simulates consequential elective registration with confirmation output."""
    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html>
<head>
    <title>Registration Confirmation</title>
    <style>
        body {{ font-family: sans-serif; background: #ecfdf5; padding: 40px; text-align: center; color: #065f46; }}
        .card {{ background: white; max-width: 500px; margin: 0 auto; padding: 30px; border-radius: 8px; border: 1px solid #a7f3d0; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        h1 {{ color: #047857; margin-top: 0; }}
        .badge {{ background: #d1fae5; color: #065f46; padding: 6px 12px; border-radius: 20px; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Registration Confirmation</h1>
        <p>Enrollment status: <span class="badge">Confirmed</span></p>
        <p>You have successfully registered for <strong>{course_code} — Digital Marketing</strong>.</p>
        <p>Reference Confirmation ID: <strong>CONF-2026-{course_code}-SUCCESS</strong></p>
        <a href="/test-portal/dashboard" style="color: #047857;">Return to Portal Dashboard</a>
    </div>
</body>
</html>""",
        status_code=200,
    )

