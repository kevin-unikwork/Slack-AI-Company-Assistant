# 🚀 Deployment Guide: Render & Railway

This guide explains how to deploy the Slack AI Bot to production using **Render** or **Railway**.

---

### 4. Single Web Service
Since the project uses **APScheduler** integrated into the FastAPI process, you only need to run the `web` service. APScheduler handles all background tasks (Reminders, Standups, Celebrations) automatically within the same process.

---

## 🏗️ Architecture Note: Background Tasks
We have migrated from Celery to **APScheduler**. 
*   **Pros**: Simplified deployment (no separate worker/beat needed), less memory usage.
*   **Cons**: If the `web` service goes to sleep (e.g., on a free tier), the scheduler stops. Ensure your service is kept awake for reliable reminders.

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
