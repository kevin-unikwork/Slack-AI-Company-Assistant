import asyncio
import os
from dotenv import load_dotenv

# Load local .env
load_dotenv()

from app.agents.policy_agent import answer_policy_question

async def test_policy():
    print("Testing policy QA...")
    try:
        answer = await answer_policy_question("What is the company policy?", "U12345")
        print(f"Bot response: {answer}")
    except Exception as e:
        print(f"Policy QA failed with error: {e}")

if __name__ == "__main__":
    asyncio.run(test_policy())
