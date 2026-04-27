import asyncio
import asyncpg
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
# Loads from .env or environment variables
LOCAL_URL = os.getenv("LOCAL_DATABASE_URL", "postgresql://postgres:1234@localhost:5432/slackbot")
REMOTE_URL = os.getenv("DATABASE_URL")

if not REMOTE_URL:
    print("Error: DATABASE_URL not found in environment or .env file.")
    sys.exit(1)

# Order of migration to respect foreign keys
TABLES_TO_MIGRATE = [
    "users",
    "policy_documents",
    "feedbacks",
    "broadcast_logs",
    "reminders",
    "leave_requests",
    "standup_summaries",
    "standup_responses"
]

async def migrate():
    print(f"🚀 Starting migration using asyncpg...")
    
    local_conn = None
    remote_conn = None
    
    try:
        # Ensure asyncpg uses postgresql://
        def fix_asyncpg_url(url):
            if url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql://", 1)
            return url

        local_conn = await asyncpg.connect(fix_asyncpg_url(LOCAL_URL))
        remote_conn = await asyncpg.connect(fix_asyncpg_url(REMOTE_URL))
        
        print("✅ Connected to both databases.")
        
        for table in TABLES_TO_MIGRATE:
            print(f"📦 Migrating table: {table}...")
            
            # Fetch data from local
            try:
                rows = await local_conn.fetch(f"SELECT * FROM {table}")
            except Exception as e:
                print(f"   ⚠️  Could not read from local table '{table}': {e}")
                continue
                
            if not rows:
                print(f"   ℹ️  Table '{table}' is empty.")
                continue
            
            # Prepare data for insertion with conflict handling
            columns = list(rows[0].keys())
            col_names = ", ".join(columns)
            placeholders = ", ".join([f"${i+1}" for i in range(len(columns))])
            
            # Use ON CONFLICT (id) DO NOTHING to skip rows that already exist on the remote
            insert_query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
            
            # Insert into remote
            async with remote_conn.transaction():
                # Convert Record objects to tuples for execute_many
                data = [tuple(row.values()) for row in rows]
                await remote_conn.executemany(insert_query, data)
                
            print(f"   ✅ Successfully migrated {len(rows)} rows to '{table}' (skipped duplicates).")

            # Reset sequence for autoincrementing IDs
            try:
                await remote_conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), MAX(id)) FROM {table}")
            except Exception:
                # Some tables might not have a sequence on 'id'
                pass

        print("\n✨ Migration completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)
    finally:
        if local_conn:
            await local_conn.close()
        if remote_conn:
            await remote_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate())
