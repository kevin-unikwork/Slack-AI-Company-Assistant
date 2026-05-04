from cryptography.fernet import Fernet
from app.config import settings

class VaultEncryption:
    def __init__(self):
        if not settings.vault_master_key:
            raise ValueError("VAULT_MASTER_KEY not found in settings")
        self.fernet = Fernet(settings.vault_master_key.encode())

    def encrypt(self, value: str) -> str:
        """Encrypt a string and return a string (base64 encoded)."""
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted string and return the original string."""
        return self.fernet.decrypt(encrypted_value.encode()).decode()

vault_encryption = VaultEncryption()
