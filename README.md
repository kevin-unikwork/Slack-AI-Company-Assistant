# 🚀 Slack AI Company Assistant

A highly advanced, production-ready Slack bot powered by **LangChain**, **OpenAI (GPT-4o)**, and **PostgreSQL (pgvector)**. Designed to automate HR workflows, manage secure employee data, and foster a connected company culture through intelligent AI agents.

---

## 🏗️ Architecture Overview

The system is built on a high-performance, purely asynchronous architecture, eliminating the need for complex message brokers like Celery or Redis.

- **FastAPI Webhook Handler**: Asynchronously receives and processes events from Slack (DMs, Mentions, Slash Commands).
- **Intent Router**: Uses `gpt-4o-mini` to intelligently route conversational messages to the correct specialized agent.
- **APScheduler**: Natively handles background cron jobs (standups, reminders, celebration checks) within the FastAPI event loop.
- **pgvector Integration**: Stores company policies and vector embeddings directly in PostgreSQL, eliminating the need for standalone vector databases like ChromaDB.
- **Symmetric Encryption at Rest**: Utilizes the `cryptography` library (Fernet) to securely encrypt sensitive user data before it ever hits the database.

---

## ✨ Key Features

### 🔐 Personal AI Vault
- **Secure Storage:** Employees can store sensitive data (API keys, passwords, links) using `/vault set <key> <secret>`.
- **Military-Grade Encryption:** Data is encrypted at rest using Fernet symmetric encryption.
- **User Isolation:** Vault data is strictly isolated; users can only ever access their own encrypted data.

### 🎉 Advanced AI Celebrations & HR Tools
- **Daily Automated Checks:** Automatically posts rich, GPT-4o generated birthday and work anniversary messages to `#general`.
- **Custom HR Templates:** HR Admins can override AI generation using `/setmessage` to define custom templates with variables like `{name}`, `{years}`, and `{date}`.
- **HR Proactive Reminders:** The system scans 24 hours ahead and automatically sends a private DM to HR Admins, reminding them of upcoming birthdays and work anniversaries so they can prepare.

### 📅 Leave Management & Approvals
- **Natural Language Requests:** Employees request leave conversationally (e.g., _"I need next Tuesday off for a doctor's appointment"_).
- **Manager Approval Flow:** Automatically identifies the employee's manager and initiates an interactive Slack Block Kit approval workflow.

### 📝 Automated Standups
- **Morning Check-ins:** Pings employees every morning (Mon-Fri) to collect their daily standup updates.
- **AI Summarization:** Automatically aggregates all responses and posts a clean, project-wise summary to the engineering channel.

### 🤖 RAG Policy Assistant
- **Instant Answers:** Ask anything about the company handbook (e.g., _"What is the remote work policy?"_).
- **Vector Search:** Uses OpenAI embeddings and PostgreSQL `pgvector` to find the exact relevant policy documents.

### 👏 Employee Kudos System
- **Peer Recognition:** Use `/kudos @user <message>` to publicly appreciate teammates.
- **Culture Building:** Fosters a positive environment by tracking and broadcasting appreciation.

### ⏰ Intelligent Reminders
- **Natural Language Parsing:** _"Remind me in 2 hours to deploy the server."_
- **Real-time Firing:** Checks every 10 seconds to deliver precise, timely DMs.

---

## 📁 Project Structure

```text
├── alembic/              # PostgreSQL database migration scripts
├── app/
│   ├── agents/           # Specialized AI Agents (Vault, Celebration, Standup, etc.)
│   ├── api/              # FastAPI routes (Slack webhooks and slash commands)
│   ├── db/               # SQLAlchemy models (User, Vault, Templates, pgvector)
│   ├── services/         # Integrations (Slack SDK)
│   ├── utils/            # Encryption modules, loggers, and exceptions
│   └── scheduler.py      # APScheduler configuration for background cron tasks
├── scripts/              # Setup tools (Sync Slack users, trigger tests)
├── .env.example          # Environment variables template
├── requirements.txt      # Python dependencies
└── alembic.ini           # Migration configuration
```

---

## 🛠️ Setup & Installation

### 1. Prerequisites
- Python 3.10+
- PostgreSQL (with `pgvector` extension enabled)
- OpenAI API Key
- Slack App configured with proper scopes and tokens

### 2. Installation
```bash
git clone https://github.com/kevin-unikwork/Slack-AI-Company-Assistant.git
cd Slack-AI-Company-Assistant
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your credentials.
**Crucial Security Note:** You must generate a secure base64 Fernet key for the `VAULT_MASTER_KEY` environment variable.

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db
VAULT_MASTER_KEY=your_base64_fernet_key
```

### 4. Database Setup
Ensure your PostgreSQL database has the `pgvector` extension installed.
```bash
alembic upgrade head
python scripts/sync_slack_workspace.py  # Populate initial user list
```

### 5. Running the Application
The application handles webhooks and background scheduling in a single, efficient process.
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 🛡️ License
Distributed under the MIT License.
