from pydantic import BaseModel
from datetime import datetime


class UserCreate(BaseModel):
    slack_id: str
    slack_username: str
    email: str | None = None
    full_name: str | None = None
    manager_slack_id: str | None = None


class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    manager_slack_id: str | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    id: int
    slack_id: str
    slack_username: str
    email: str | None
    full_name: str | None
    is_hr_admin: bool
    is_active: bool
    joined_at: datetime

    model_config = {"from_attributes": True}