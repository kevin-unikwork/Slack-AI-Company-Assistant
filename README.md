# 🚀 Slack AI Company Assistant

A highly advanced, production-ready Slack bot powered by **LangChain**, **OpenAI (GPT-4o)**, and **PostgreSQL (pgvector)**. Designed to automate HR workflows, manage secure employee data, and foster a connected company culture through intelligent AI agents.

---

## 🏗️ Architecture & Tech Stack

The system is built on a high-performance, purely asynchronous architecture, eliminating the need for complex message brokers like Celery or Redis.

- **Framework**: FastAPI (Asynchronous Webhook Handler)
- **AI/LLM Engine**: OpenAI `gpt-4o` and `gpt-4o-mini` orchestrated via LangChain
- **Database**: PostgreSQL with `pgvector` for vector embeddings
- **Task Scheduling**: `APScheduler` (Runs natively inside the FastAPI event loop)
- **Security**: `cryptography` (Fernet Symmetric Encryption at Rest)
- **Slack SDK**: `slack_bolt` (Asyncio mode)

### Core System Flow
1. **Event Ingestion**: FastAPI receives events from Slack (DMs, Mentions, Slash Commands).
2. **Intent Routing**: Conversational messages are passed to the `gpt-4o-mini` Intent Router, which intelligently routes the request to the correct specialized agent.
3. **Agent Execution**: The specialized agent processes the request (e.g., querying the database, generating a response, executing a workflow) and replies asynchronously.

---

## ✨ Exhaustive Feature List

### 🔐 Personal AI Vault (`/vault`)
A highly secure, isolated digital vault for employees to store sensitive data.
- **Commands**:
  - `/vault set <key> <secret>`: Store a new encrypted secret.
  - `/vault get <key>`: Retrieve a secret (sent via ephemeral DM).
  - `/vault list`: View all stored keys.
  - `/vault delete <key>`: Remove a secret permanently.
- **Security**: Data is encrypted using Fernet symmetric encryption before insertion into the database. Users cannot access other users' vaults.

### 🎉 Advanced AI Celebrations & HR Controls
Automates company culture while giving HR full control.
- **Automated AI Generation**: Every morning at 9:00 AM IST, the bot posts rich, GPT-4o generated birthday and work anniversary messages to the `#general` channel.
- **HR Proactive Reminders**: The system scans 24 hours ahead and automatically sends a private DM to HR Admins, reminding them of upcoming birthdays and work anniversaries so they can prepare.
- **Custom Templates (`/setmessage`)**: HR can override AI generation with custom templates.
  - `/setmessage set birthday Happy Birthday {name}! 🎂`
  - `/setmessage set anniversary Congrats {name} on {years} years!`
  - `/setmessage view birthday` (Preview current template)
  - `/setmessage reset birthday` (Revert to AI generation)
- **Date Management (`/setbirthday`, `/setanniversary`)**: HR can set dates for users using robust natural language and Unicode parsing.

### 📅 Leave Management & Approvals
- **Natural Language Requests**: Employees request leave conversationally (e.g., _"I need next Tuesday off for a doctor's appointment"_).
- **Automated Routing**: The Leave Agent extracts the dates, checks the user's manager, and sends an interactive Slack Block Kit approval request directly to the manager.
- **Approval Tracking**: Approved/Rejected states are tracked in PostgreSQL.

### 📝 Automated Standups
- **Morning Check-ins**: Pings employees every morning (Mon-Fri) to collect their daily updates.
- **AI Summarization**: Automatically aggregates all responses and posts a clean, project-wise summary to the engineering channel.

### 🤖 RAG Policy Assistant
- **Instant Answers**: Ask anything about the company handbook (e.g., _"What is the remote work policy?"_ or _"How many sick leaves do we get?"_).
- **Vector Search Engine**: Uses OpenAI `text-embedding-3-small` and PostgreSQL `pgvector` to perform semantic searches across company documents.

### 👏 Employee Kudos System (`/kudos`)
- **Peer Recognition**: Use `/kudos <@user> <message>` to publicly appreciate teammates.
- **Culture Building**: Fosters a positive environment by tracking and broadcasting appreciation to the general channel.

### ⏰ Intelligent Reminders
- **Natural Language Parsing**: Tell the bot _"Remind me in 2 hours to deploy the server"_ or _"Remind me tomorrow at 10 AM to call the client."_
- **Real-time Firing**: The APScheduler checks the database every 10 seconds to deliver precise, timely DMs.

---

## 📁 Project Structure

```text
├── alembic/              # PostgreSQL database migration scripts
├── app/
│   ├── agents/           # Specialized AI Agents (Vault, Celebration, Standup, Leave, Policy)
│   ├── api/              # FastAPI routes (Slack webhooks and slash commands)
│   ├── db/               # SQLAlchemy models (User, Vault, Templates, pgvector)
│   ├── services/         # Integrations (Slack SDK initialization)
│   ├── utils/            # Encryption modules, loggers, and custom exceptions
│   ├── config.py         # Pydantic BaseSettings environment loader
│   └── scheduler.py      # APScheduler configuration for background cron tasks
├── scripts/              # Setup tools (Sync Slack users, trigger manual tests)
├── .env.example          # Environment variables template
├── requirements.txt      # Python dependencies
└── alembic.ini           # Migration configuration
```

---

## 🛠️ Setup, Installation, & Deployment

### 1. Prerequisites
- Python 3.10+
- PostgreSQL (with `pgvector` extension enabled)
- OpenAI API Key
- Slack App configured with proper scopes and Socket Mode / Event Subscriptions.

### 2. Local Installation
```bash
git clone https://github.com/kevin-unikwork/Slack-AI-Company-Assistant.git
cd Slack-AI-Company-Assistant
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file based on `.env.example`.

**Crucial Security Note**: You must generate a secure base64 Fernet key for the `VAULT_MASTER_KEY` environment variable. You can generate one by running:
```python
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Required `.env` Variables**:
```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/dbname
VAULT_MASTER_KEY=your_generated_base64_key
```

### 4. Database Initialization
Ensure your PostgreSQL database has the `pgvector` extension installed. Run the migrations to build the schema:
```bash
alembic upgrade head
```
Then, populate your database with your company's Slack users:
```bash
python scripts/sync_slack_workspace.py
```

### 5. Running the Application
The application is designed to handle webhooks, AI generation, and background scheduling all within a single, highly-efficient ASGI process.
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 6. Slack App Dashboard Configuration
You must configure the following in your Slack App Dashboard:
- **Event Subscriptions**: Enable events and subscribe to `app_mention` and `message.im`. Point the Request URL to your server's `/slack/events` endpoint.
- **Interactivity**: Enable Interactivity and point the Request URL to `/slack/events`.
- **Slash Commands**: Register the following commands:
  - `/vault`
  - `/kudos`
  - `/setmessage` (HR Admin only)
  - `/setbirthday` (HR Admin only)
  - `/setanniversary` (HR Admin only)
  - `/triggercelebration` (HR Admin only)
- **Scopes**: `app_mentions:read`, `channels:history`, `chat:write`, `commands`, `im:history`, `users:read`, `users:read.email`.

---

## 🛡️ License
Distributed under the MIT License.
