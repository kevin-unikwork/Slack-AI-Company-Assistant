import asyncio
import os
import sys

# Ensure the app module can be found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.agents.standup_agent import post_standup_summary

async def run():
    print("Triggering standup summary generation...")
    await post_standup_summary()
    print("Done! Check your Slack channels and DMs.")

if __name__ == "__main__":
    asyncio.run(run())
