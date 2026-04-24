# 🚀 Deployment Guide: Render & Railway

This guide explains how to deploy the Slack AI Bot to production using **Render** or **Railway**.

---

## 🏗️ Deployment Architecture
To run the full system, you need 3 active processes:
1.  **Web Server**: Handles Slack Webhooks (FastAPI).
2.  **Worker**: Processes background tasks and AI generations (Celery).
3.  **Beat**: Schedules recurring tasks like Standups and Celebrations (Celery Beat).

Plus 2 managed services:
*   **PostgreSQL**: For persistent data.
*   **Redis**: For Celery task brokering and state.

---

## 🛤️ Option 1: Deploying on Railway (Recommended)
Railway is highly recommended for this project as it handles multi-process deployments and managed databases very well.

### 1. Connect your GitHub
1.  Go to [Railway.app](https://railway.app/) and create a new project.
2.  Select **"Deploy from GitHub repo"** and choose your bot repository.

### 2. Add Managed Databases
1.  In your Railway project, click **"New"** -> **"Database"** -> **"Add PostgreSQL"**.
2.  Click **"New"** -> **"Database"** -> **"Add Redis"**.

### 3. Configure Environment Variables
Railway will automatically provide `DATABASE_URL` and `REDIS_URL`. You need to manually add:
*   `SLACK_BOT_TOKEN`
*   `SLACK_SIGNING_SECRET`
*   `OPENAI_API_KEY`
*   `ONBOARDING_WELCOME_CHANNEL` (Your Slack Channel ID)

### 4. Setup Multiple Services
Since we have a `Procfile`, Railway will detect the `web`, `worker`, and `beat` commands. 
*   Railway might start the `web` service by default. 
*   You may need to duplicate the service (using the same repo) and change the **Start Command** to `celery -A app.core.celery_app worker --loglevel=info` for the worker, and another for the beat.

---

## ☁️ Option 2: Deploying on Render (Blueprints)
Render uses a `render.yaml` file to automate the setup of multiple services.

### 1. Create a `render.yaml`
I have already created a basic `Procfile`. For Render, you can use a Blueprint to spin up the API, Worker, and Databases together.

### 2. Required Environment Variables on Render
When you connect your GitHub repo to Render:
1.  Select **Blueprint** or **Web Service**.
2.  Add your secrets in the **Environment** tab.
3.  Make sure to set `DATABASE_URL` to point to your Render PostgreSQL instance.

---

## 🛠️ Post-Deployment Steps

### 1. Database Migrations
Once deployed, you need to run the migrations on the production database. You can do this via the Railway/Render terminal:
```bash
alembic upgrade head
```

### 2. Update Slack App Settings
1.  Go to [Slack API Dashboard](https://api.slack.com/apps).
2.  In **Event Subscriptions**, update the Request URL to:
    `https://your-app-url.railway.app/slack/events`
3.  In **Interactivity & Shortcuts**, update the Request URL to the same endpoint.
4.  Update the URL for all **Slash Commands** as well.

### 3. Sync Workspace
Run the sync script once on production to pull your Slack team members into the database:
```bash
python scripts/sync_slack_workspace.py
```

---

## 💡 Pro Tip (Free Tiers)
Most free tiers (like Render's) put the "Web" service to sleep after inactivity. However, the **Celery Worker** and **Beat** need to stay awake for reminders and standups to work. 

**Recommendation**: If you are using a strictly free tier, consider using **Railway's trial credits** or **Render's Hobby plan** for the Worker/Beat services to ensure 24/7 reliability.
