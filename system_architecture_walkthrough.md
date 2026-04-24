# 🏗️ Slack AI Bot: System Architecture & Implementation Guide

This guide provides a detailed walkthrough of how the Slack AI Bot is implemented, the technologies used, and how each module performs its role.

---

## 🛠️ Core Technology Stack

The system is built using a modern, scalable AI stack:

1.  **FastAPI (Python)**: The high-performance web framework used for the Slack webhook endpoint and internal APIs.
2.  **LangChain**: The orchestration layer that manages AI prompts, chains, and vector store integrations.
3.  **OpenAI (GPT-4o & GPT-4o-mini)**: 
    *   **GPT-4o**: Used for complex tasks like generating personalized greetings and answering policy questions.
    *   **GPT-4o-mini**: Used for lightning-fast intent classification.
4.  **ChromaDB**: The vector database used for **RAG (Retrieval-Augmented Generation)** to store and search company policy documents.
5.  **PostgreSQL**: The relational database for storing user profiles, leave requests, reminders, and feedback.
6.  **Celery + Redis**: The background task engine used for scheduled events (Standups, Celebrations) and delayed reminders.
7.  **Slack Bolt SDK**: The official Slack framework for handling events, commands, and interactive blocks.

---

## 🤖 Module Deep-Dive

### 1. Intent Router (The Traffic Controller)
*   **Technique**: Few-shot classification using `gpt-4o-mini`.
*   **Performance**: It analyzes every incoming DM and determines if the user wants to apply for leave, ask a question, or just chat. 
*   **Logic**: It first checks the "User State" (e.g., if a user is currently in the middle of a leave application) to maintain context without needing to ask the AI every time.

### 2. Policy Agent (The Knowledge Expert)
*   **Technique**: RAG (Retrieval-Augmented Generation).
*   **Implementation**: 
    1.  Documents (PDF/Text) are chunked and embedded into vectors.
    2.  When a question is asked, the bot searches ChromaDB for the most relevant 4-5 snippets.
    3.  The AI "reads" these snippets and generates a formatted response, citing the source document.
*   **Optimization**: Includes special instructions to handle "vertical text" often found in PDF tables.

### 3. Leave Agent (The Stateful Manager)
*   **Technique**: Finite State Machine (FSM).
*   **Performance**: It tracks the conversation state (e.g., `AWAITING_START_DATE`, `AWAITING_REASON`). It validates dates and reason length before persisting the request to Postgres and notifying the manager via interactive buttons.

### 4. Celebration Agent (The Culture Booster)
*   **Technique**: AI Persona Generation.
*   **Implementation**: A daily Celery task checks for birthdays/anniversaries. If found, it uses GPT-4o with a "Warm & Professional" persona to write a unique message every time, ensuring the greetings never feel repetitive.

### 5. Reminder Agent (The NLP Timekeeper)
*   **Technique**: Natural Language Understanding (NLU).
*   **Performance**: It parses phrases like "Remind me in 2 hours" or "at 3 PM tomorrow" into a standard UTC timestamp. It then schedules a Celery task to DM the user at the exact moment.

---

## 🔄 Integration & Performance Techniques

*   **Asynchronous Processing**: Every Slack event is acknowledged within 200ms (to prevent Slack's "Operation Timed Out" error) and then processed in an `asyncio.create_task`.
*   **Interactive UI (Block Kit)**: We use Slack's "Block Kit" for beautiful buttons and sections, making the bot feel like a modern application rather than just a text-based terminal.
*   **Immediate Feedback Loop**: The system sends an initial "Thinking..." message and updates it as the AI progresses, ensuring the user is never left waiting in silence.
*   **Database Migrations**: Uses **Alembic** to ensure the database schema stays in sync across different development and production environments.

---

## 🚀 Why this is "Production-Ready"

1.  **Fault Tolerance**: Background tasks are retried automatically.
2.  **Scalability**: Can handle hundreds of users due to its async nature and Redis-backed state management.
3.  **Security**: Uses Slack Signing Secret verification and enforces HR-Admin permissions for sensitive commands.
4.  **Maintainability**: Decoupled agents mean you can update the Policy Agent without touching the Leave logic.
