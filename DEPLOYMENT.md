# CampusPilot Deployment Guide

CampusPilot is architected as an autonomous browser AI agent with a **FastAPI + Playwright backend** and a **Vite + React frontend**.

This guide explains how to deploy:
- **Backend**: [Render](https://render.com) (Docker Web Service with Playwright Chromium)
- **Frontend**: [Vercel](https://vercel.com) (Static Site with SPA routing)

---

## Part 1: Deploy Backend to Render

### Option A: 1-Click Render Blueprint (Recommended)
1. Fork or push this repository to GitHub.
2. Sign in to [Render Dashboard](https://dashboard.render.com).
3. Click **New +** > **Blueprint**.
4. Select your repository (`AI-Hackathon`).
5. Render will detect `render.yaml` automatically.
6. Configure the prompted environment variables:
   * `GROQ_API_KEY`: Your Groq API key (`gsk_...`)
   * `PORTAL_URL`: (Optional) College or test portal base URL
   * `TEAM_CODE`: (Optional) Your team code
7. Click **Apply**. Render will build the container using the official Microsoft Playwright image and deploy.
8. Once deployed, copy your backend URL (e.g. `https://campuspilot-backend.onrender.com`).

---

### Option B: Manual Web Service on Render
If deploying without Blueprint:
1. In Render Dashboard, click **New +** > **Web Service**.
2. Connect your GitHub repository.
3. Configure settings:
   * **Name**: `campuspilot-backend`
   * **Language / Environment**: `Docker`
   * **Dockerfile Path**: `./backend/Dockerfile`
   * **Docker Build Context**: `./backend`
   * **Plan**: Starter (or Free)
4. Under **Environment Variables**, add:
   | Key | Value | Notes |
   |---|---|---|
   | `GROQ_API_KEY` | `gsk_...` | From [Groq Console](https://console.groq.com/keys) |
   | `GROQ_MODEL` | `openai/gpt-oss-20b` | Or any supported Groq model |
   | `LLM_PROVIDER` | `groq` | |
   | `BROWSER_HEADLESS` | `true` | Headless execution on cloud VM |
   | `APP_ENV` | `production` | |
   | `FRONTEND_URL` | `https://your-frontend.vercel.app` | Your Vercel frontend URL |
   | `CORS_ORIGINS` | `https://*.vercel.app` | Allows all Vercel preview & production URLs |
5. Click **Create Web Service**. Verify health check at `https://your-backend.onrender.com/health`.

---

## Part 2: Deploy Frontend to Vercel

1. Sign in to [Vercel Dashboard](https://vercel.com).
2. Click **Add New...** > **Project**.
3. Import your GitHub repository (`AI-Hackathon`).
4. In the configuration screen:
   * **Framework Preset**: `Vite`
   * **Root Directory**: Click *Edit* and select `Frontend` (or leave as root; both have `vercel.json` configured).
   * **Build Command**: `npm run build`
   * **Output Directory**: `dist`
5. Under **Environment Variables**, add:
   | Key | Value |
   |---|---|
   | `VITE_AUTOMATION_API_URL` | `https://your-backend.onrender.com` (from Part 1) |
6. Click **Deploy**.
7. Vercel will build and assign a domain (e.g., `https://campuspilot.vercel.app`).

---

## Part 3: Link & Verify Connection

1. In your **Render Dashboard** for the backend service:
   * Go to **Environment**.
   * Set `FRONTEND_URL` to your Vercel URL (e.g., `https://campuspilot.vercel.app`).
   * Save changes (triggers an instant re-deploy with updated CORS headers).
2. Open your Vercel frontend in your browser.
3. Check the status indicator in the top right:
   * It should read **Playwright Connected** (Green dot).
4. Run a test task to verify end-to-end autonomous agent workflow.

---

## Deployment Architecture

```
┌─────────────────────────────────┐
│         Vercel Frontend         │
│     (React 18 + Vite SPA)       │
│   https://*.vercel.app          │
└────────────────┬────────────────┘
                 │
                 │ HTTPS REST / Server-Sent Events (SSE)
                 │ CORS: allow_origin_regex="https://.*\.vercel\.app"
                 ▼
┌─────────────────────────────────┐
│         Render Backend          │
│       (FastAPI + Uvicorn)       │
│  mcr.microsoft.com/playwright   │
│   https://*.onrender.com        │
├────────────────┬────────────────┤
│   Playwright   │   Groq LLM     │
│   (Chromium)   │  (High Speed)  │
└────────────────┴────────────────┘
```

