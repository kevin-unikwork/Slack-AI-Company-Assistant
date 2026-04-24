# 🚀 Slack AI Company Assistant

A powerful, production-ready Slack bot powered by **LangChain**, **OpenAI (GPT-4o)**, and **PostgreSQL**. Designed to automate HR tasks, daily standups, company policy search, and intelligent reminders.

---

## 🏗️ Architecture Overview

The system is built with a modular agent-based architecture:

- **FastAPI Webhook Handler**: Receives events from Slack (DMs, Mentions, Commands).
- **Intent Router**: Uses `gpt-4o-mini` to intelligently route messages to the correct agent.
- **RAG Policy Agent**: Uses **ChromaDB** vector search to answer questions from the company handbook.
- **Leave Agent**: Manages multi-turn conversations for leave applications and manager approvals.
- **Standup Agent**: Collects daily updates and posts formatted summaries.
- **Reminder Agent**: Parses natural language to set precise reminders.
- **Celebration Agent**: Automatically generates AI greetings for birthdays and work anniversaries.
- **Background Workers**: **Celery + Redis** handles scheduled tasks (standup triggers, celebration checks).

---

## 📁 Project Structure

```text
├── alembic/              # Database migration scripts
├── app/
│   ├── agents/           # AI Logic for different features
│   ├── api/              # FastAPI routes (Slack webhooks)
│   ├── core/             # Background task logic & scheduling
│   ├── db/               # SQLAlchemy models & database sessions
│   ├── schemas/          # Pydantic models for validation
│   ├── services/         # Integrations (Slack SDK, ChromaDB)
│   └── utils/            # Shared loggers & exceptions
├── scripts/              # Essential maintenance & setup tools
├── tests/                # Unit and integration tests
├── .env.example          # Template for environment variables
├── requirements.txt      # Python dependencies
└── alembic.ini           # Migration configuration
```

---

## ✨ Key Features

- **🤖 AI Policy Assistant**: Ask anything about the company handbook.
- **📅 Leave Management**: Natural language leave requests with automated manager approval flow.
- **📝 Automated Standups**: Morning check-ins with project-wise summary posting.
- **⏰ Natural Language Reminders**: _"Remind me in 2 hours to check the logs."_
- **🎉 AI Celebrations**: Personalized, GPT-4o generated birthday and anniversary greetings.
- **🤫 Anonymous Feedback**: Securely send suggestions to HR.
- **🛡️ Admin Tools**: HR-only commands for announcements and hierarchy management.

---

## 🛠️ Setup & Installation

### 1. Prerequisites
- Python 3.10+
- PostgreSQL
- Redis (for Celery & state)
- OpenAI API Key

### 2. Installation
```bash
git clone https://github.com/your-repo/slack-ai-bot.git
cd slack-ai-bot
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your credentials:
- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `OPENAI_API_KEY`
- `DATABASE_URL`

### 4. Database Setup
```bash
alembic upgrade head
python scripts/sync_slack_workspace.py  # Populate initial user list
```

### 5. Running the Application
```bash
# Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Start Celery Worker (in a separate terminal)
celery -A app.core.celery_app worker --loglevel=info

# Start Celery Beat (for scheduled tasks)
celery -A app.core.celery_app beat --loglevel=info
```

---

## 🛡️ License
Distributed under the MIT License. See `LICENSE` for more information.
