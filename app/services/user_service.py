import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.utils.logger import get_logger
from app.utils.exceptions import UserNotFoundError, DatabaseError

logger = get_logger(__name__)


class UserService:
    """CRUD operations for employee/user records."""

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    async def get_by_slack_id(self, session: AsyncSession, slack_id: str) -> User | None:
        result = await session.execute(select(User).where(User.slack_id == slack_id))
        return result.scalar_one_or_none()

    async def get_by_slack_username(self, session: AsyncSession, username: str) -> User | None:
        result = await session.execute(select(User).where(User.slack_username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_all_active(self, session: AsyncSession) -> list[User]:
        result = await session.execute(select(User).where(User.is_active == True))
        return list(result.scalars().all())

    async def get_all(
        self,
        session: AsyncSession,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[User], int]:
        from sqlalchemy import func

        total_result = await session.execute(select(func.count()).select_from(User))
        total: int = total_result.scalar_one()

        result = await session.execute(
            select(User)
            .order_by(User.joined_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    async def create_or_update(self, session: AsyncSession, data: UserCreate) -> User:
        """Upsert a user by slack_id — used during onboarding and team_join events."""
        try:
            existing = await self.get_by_slack_id(session, data.slack_id)
            if existing:
                existing.slack_username = data.slack_username
                if data.email:
                    existing.email = data.email
                if data.full_name:
                    existing.full_name = data.full_name
                if data.manager_slack_id:
                    existing.manager_slack_id = data.manager_slack_id
                await session.flush()
                logger.info("User updated", extra={"slack_id": data.slack_id})
                return existing

            user = User(
                slack_id=data.slack_id,
                slack_username=data.slack_username,
                email=data.email,
                full_name=data.full_name,
                manager_slack_id=data.manager_slack_id,
            )
            session.add(user)
            await session.flush()
            logger.info("User created", extra={"slack_id": data.slack_id})
            return user
        except Exception as exc:
            logger.exception("Failed to create/update user", extra={"slack_id": data.slack_id})
            raise DatabaseError(f"User upsert failed: {exc}") from exc

    async def update_user(
        self,
        session: AsyncSession,
        slack_id: str,
        data: UserUpdate,
    ) -> User:
        user = await self.get_by_slack_id(session, slack_id)
        if not user:
            raise UserNotFoundError(f"User {slack_id} not found")
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(user, field, value)
        await session.flush()
        return user

    async def set_admin(
        self,
        session: AsyncSession,
        slack_id: str,
        is_admin: bool,
    ) -> User:
        user = await self.get_by_slack_id(session, slack_id)
        if not user:
            raise UserNotFoundError(f"User {slack_id} not found")
        user.is_hr_admin = is_admin
        await session.flush()
        logger.info(
            "Admin status changed",
            extra={"slack_id": slack_id, "is_hr_admin": is_admin},
        )
        return user

    async def set_password(
        self,
        session: AsyncSession,
        slack_id: str,
        plain_password: str,
    ) -> None:
        """Hash and store password for HR admin login."""
        user = await self.get_by_slack_id(session, slack_id)
        if not user:
            raise UserNotFoundError(f"User {slack_id} not found")
        hashed = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()
        user.hashed_password = hashed
        await session.flush()

    def verify_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode(), hashed.encode())


user_service = UserService()