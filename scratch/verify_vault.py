import asyncio
import sys
sys.path.insert(0, ".")

from app.db.session import init_db
from app.agents.vault_agent import add_to_vault, list_vault, get_from_vault, delete_from_vault

async def test_vault():
    print("Initializing Database...")
    await init_db()
    
    test_user = "U12345TEST"
    
    print("\n--- Testing SET ---")
    res = await add_to_vault(test_user, "OpenAI_Key", "sk-test-123456789")
    print(res)
    
    res = await add_to_vault(test_user, "Figma_Project", "https://figma.com/file/xyz")
    print(res)

    print("\n--- Testing LIST ---")
    res = await list_vault(test_user)
    print(res)

    print("\n--- Testing GET ---")
    res = await get_from_vault(test_user, "OpenAI_Key")
    print(res)
    
    res = await get_from_vault(test_user, "NonExistent")
    print(res)

    print("\n--- Testing DELETE ---")
    res = await delete_from_vault(test_user, "Figma_Project")
    print(res)
    
    print("\n--- Testing LIST after delete ---")
    res = await list_vault(test_user)
    print(res)

if __name__ == "__main__":
    asyncio.run(test_vault())
