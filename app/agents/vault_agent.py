from sqlalchemy import select, delete
from app.db.session import AsyncSessionLocal
from app.db.models.vault import UserVault
from app.utils.encryption import vault_encryption
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

async def add_to_vault(slack_id: str, key_name: str, value: str, category: str = None) -> str:
    """Store an encrypted secret in the user's vault."""
    encrypted = vault_encryption.encrypt(value)
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check if key already exists for this user
            result = await session.execute(
                select(UserVault).where(
                    UserVault.user_slack_id == slack_id,
                    UserVault.key_name == key_name
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                existing.encrypted_value = encrypted
                existing.category = category
                msg = f":arrows_counterclockwise: Updated *{key_name}* in your vault."
            else:
                new_entry = UserVault(
                    user_slack_id=slack_id,
                    key_name=key_name,
                    encrypted_value=encrypted,
                    category=category
                )
                session.add(new_entry)
                msg = f":lock: Successfully stored *{key_name}* in your private vault."
                
    return msg

async def list_vault(slack_id: str) -> str:
    """List all keys stored in the user's vault."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserVault.key_name, UserVault.category)
            .where(UserVault.user_slack_id == slack_id)
            .order_by(UserVault.key_name)
        )
        entries = result.all()
        
    if not entries:
        return ":question: Your vault is empty. Use `/vault set <name> <secret>` to add something."
    
    lines = [":file_folder: *Your Private Vault Keys:*"]
    for name, cat in entries:
        cat_str = f" [{cat}]" if cat else ""
        lines.append(f"• *{name}*{cat_str}")
        
    lines.append("\nUse `/vault get <name>` to retrieve a secret.")
    return "\n".join(lines)

async def get_from_vault(slack_id: str, key_name: str) -> str:
    """Retrieve and decrypt a secret from the user's vault."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserVault)
            .where(
                UserVault.user_slack_id == slack_id,
                UserVault.key_name == key_name
            )
        )
        entry = result.scalar_one_or_none()
        
    if not entry:
        return f":x: Key *{key_name}* not found in your vault."
    
    try:
        decrypted = vault_encryption.decrypt(entry.encrypted_value)
        return f":key: *{key_name}*: `{decrypted}`\n_This message is only visible to you._"
    except Exception as e:
        logger.error(f"Failed to decrypt vault entry for {slack_id}: {e}")
        return ":warning: Failed to decrypt secret. Please contact admin."

async def delete_from_vault(slack_id: str, key_name: str) -> str:
    """Delete an entry from the user's vault."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                delete(UserVault).where(
                    UserVault.user_slack_id == slack_id,
                    UserVault.key_name == key_name
                )
            )
            if result.rowcount > 0:
                return f":trash_can: Deleted *{key_name}* from your vault."
            else:
                return f":x: Key *{key_name}* not found."
