import asyncio
import os
from dotenv import load_dotenv

# Load local .env
load_dotenv()

from app.agents.intent_router import classify_intent, Intent

async def test_classification():
    print("Testing intent classification...")
    try:
        intent = await classify_intent("U12345", "hello")
        print(f"Classification result: {intent}")
    except Exception as e:
        print(f"Classification failed with error: {e}")

if __name__ == "__main__":
    asyncio.run(test_classification())
