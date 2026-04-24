from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Any


class HRLoginRequest(BaseModel):
    email: str
    password: str


class HRLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class BroadcastRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class BroadcastResponse(BaseModel):
    sent_to: int
    failed: int
    broadcast_id: int


class PolicyUploadResponse(BaseModel):
    document_id: int
    filename: str
    chunk_count: int
    message: str


class PolicyDocumentOut(BaseModel):
    id: int
    filename: str
    original_filename: str
    file_type: str
    chunk_count: int
    uploaded_by_slack_id: str | None
    description: str | None
    uploaded_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class StandupSummaryOut(BaseModel):
    id: int
    date: datetime
    summary_text: str
    channel_id: str
    responded_count: int
    total_count: int
    posted_at: datetime | None

    model_config = {"from_attributes": True}


class LeaveRequestOut(BaseModel):
    id: int
    user_slack_id: str
    manager_slack_id: str | None
    start_date: datetime
    end_date: datetime
    reason: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaveStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")


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


class AdminToggleResponse(BaseModel):
    slack_id: str
    is_hr_admin: bool
    message: str


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int