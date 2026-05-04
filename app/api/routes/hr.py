import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.auth import create_access_token, require_hr_admin, get_current_user_payload
from app.config import settings
from app.db.session import get_session
from app.db.models.user import User
from app.db.models.standup import StandupSummary
from app.db.models.leave import LeaveRequest
from app.schemas.hr import (
    HRLoginRequest, HRLoginResponse,
    BroadcastRequest, BroadcastResponse,
    PolicyUploadResponse, PolicyDocumentOut,
    StandupSummaryOut, LeaveRequestOut, LeaveStatusUpdate,
    UserOut, AdminToggleResponse, PaginatedResponse,
)
from app.services.user_service import user_service
from app.services.policy_service import policy_service
from app.agents.broadcast_agent import send_broadcast
from app.agents import policy_agent as policy_agent_module
from app.utils.logger import get_logger
from app.utils.exceptions import UserNotFoundError, DocumentNotFoundError, AuthorizationError

logger = get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[dict, Depends(require_hr_admin)]
AuthDep = Annotated[dict, Depends(get_current_user_payload)]


# ------------------------------------------------------------------ #
# Auth                                                                 #
# ------------------------------------------------------------------ #

@router.post("/auth/login", response_model=HRLoginResponse)
async def hr_login(body: HRLoginRequest, session: SessionDep) -> HRLoginResponse:
    """Login with email + password. Returns JWT access token."""
    user = await user_service.get_by_email(session, body.email)
    if not user or not user.hashed_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user_service.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_hr_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an HR admin")

    token = create_access_token({
        "sub": user.slack_id,
        "email": user.email,
        "is_hr_admin": user.is_hr_admin,
    })
    logger.info("HR admin logged in", extra={"email": body.email, "slack_id": user.slack_id})
    return HRLoginResponse(
        access_token=token,
        expires_in_minutes=settings.jwt_expire_minutes,
    )


# ------------------------------------------------------------------ #
# Policy Documents                                                     #
# ------------------------------------------------------------------ #

@router.post("/policy/upload", response_model=PolicyUploadResponse)
async def upload_policy(
    session: SessionDep,
    admin: AdminDep,
    file: UploadFile = File(...),
    description: str = Form(default=""),
) -> PolicyUploadResponse:
    """Upload a PDF or TXT policy document. Ingests into pgvector."""
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "txt"):
        raise HTTPException(status_code=400, detail="Only PDF and TXT files are supported")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    async with session.begin():
        doc = await policy_service.ingest_document(
            session=session,
            file_bytes=file_bytes,
            original_filename=filename,
            file_type=ext,
            uploaded_by_slack_id=admin.get("sub"),
            description=description or None,
        )

    # Reset the QA chain so it picks up new documents
    policy_agent_module.reset_chain()

    logger.info("Policy uploaded via HR API", extra={"filename": filename, "doc_id": doc.id})
    return PolicyUploadResponse(
        document_id=doc.id,
        filename=doc.original_filename,
        chunk_count=doc.chunk_count,
        message=f"Successfully indexed {doc.chunk_count} chunks from '{filename}'",
    )


@router.get("/policy/list", response_model=list[PolicyDocumentOut])
async def list_policies(session: SessionDep, admin: AdminDep) -> list[PolicyDocumentOut]:
    docs = await policy_service.list_documents(session)
    return [PolicyDocumentOut.model_validate(d) for d in docs]


@router.delete("/policy/{doc_id}", status_code=204)
async def delete_policy(doc_id: int, session: SessionDep, admin: AdminDep) -> None:
    try:
        async with session.begin():
            await policy_service.delete_document(session, doc_id)
        policy_agent_module.reset_chain()
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ------------------------------------------------------------------ #
# Broadcast                                                            #
# ------------------------------------------------------------------ #

@router.post("/broadcast", response_model=BroadcastResponse)
async def broadcast(body: BroadcastRequest, session: SessionDep, admin: AdminDep) -> BroadcastResponse:
    sender_slack_id: str = admin["sub"]
    async with session.begin():
        result = await session.execute(select(User).where(User.slack_id == sender_slack_id))
        sender_user = result.scalar_one_or_none()
        if not sender_user:
            raise HTTPException(status_code=404, detail="Sender user not found")
        data = await send_broadcast(session, sender_slack_id, body.message, sender_user)
    return BroadcastResponse(**data)


# ------------------------------------------------------------------ #
# Standup                                                              #
# ------------------------------------------------------------------ #

@router.get("/standup/today", response_model=StandupSummaryOut | None)
async def today_standup(session: SessionDep, admin: AdminDep):
    from datetime import date
    from sqlalchemy import func as sqlfunc
    result = await session.execute(
        select(StandupSummary)
        .where(sqlfunc.date(StandupSummary.date) == date.today())
        .order_by(StandupSummary.posted_at.desc())
    )
    summary = result.scalars().first()
    if not summary:
        return None
    return StandupSummaryOut.model_validate(summary)


@router.get("/standup/history", response_model=PaginatedResponse)
async def standup_history(
    session: SessionDep,
    admin: AdminDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse:
    total_result = await session.execute(
        select(func.count()).select_from(StandupSummary)
    )
    total: int = total_result.scalar_one()
    result = await session.execute(
        select(StandupSummary)
        .order_by(StandupSummary.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    summaries = list(result.scalars().all())
    return PaginatedResponse(
        items=[StandupSummaryOut.model_validate(s) for s in summaries],
        total=total,
        page=page,
        page_size=page_size,
    )


# ------------------------------------------------------------------ #
# Users                                                                #
# ------------------------------------------------------------------ #

@router.get("/users", response_model=PaginatedResponse)
async def list_users(
    session: SessionDep,
    admin: AdminDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse:
    users, total = await user_service.get_all(session, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[UserOut.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users/{slack_id}/admin", response_model=AdminToggleResponse)
async def toggle_admin(
    slack_id: str,
    session: SessionDep,
    admin: AdminDep,
    grant: bool = Query(default=True, description="True to grant, False to revoke"),
) -> AdminToggleResponse:
    try:
        async with session.begin():
            user = await user_service.set_admin(session, slack_id, grant)
        action = "granted" if grant else "revoked"
        return AdminToggleResponse(
            slack_id=slack_id,
            is_hr_admin=user.is_hr_admin,
            message=f"HR admin access {action} for {slack_id}",
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ------------------------------------------------------------------ #
# Leave Requests                                                       #
# ------------------------------------------------------------------ #

@router.get("/leave/requests", response_model=list[LeaveRequestOut])
async def list_leave_requests(
    session: SessionDep,
    admin: AdminDep,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[LeaveRequestOut]:
    q = select(LeaveRequest).order_by(LeaveRequest.created_at.desc())
    if status_filter:
        q = q.where(LeaveRequest.status == status_filter)
    result = await session.execute(q)
    return [LeaveRequestOut.model_validate(r) for r in result.scalars().all()]


@router.patch("/leave/{leave_id}/status", response_model=LeaveRequestOut)
async def update_leave_status(
    leave_id: int,
    body: LeaveStatusUpdate,
    session: SessionDep,
    admin: AdminDep,
) -> LeaveRequestOut:
    from datetime import datetime, timezone
    async with session.begin():
        result = await session.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id))
        leave = result.scalar_one_or_none()
        if not leave:
            raise HTTPException(status_code=404, detail=f"Leave request {leave_id} not found")
        leave.status = body.status
        leave.resolved_at = datetime.now(timezone.utc)

    # Notify employee
    from app.services.slack_service import slack_service
    emoji = ":white_check_mark:" if body.status == "approved" else ":x:"
    try:
        await slack_service.dm_user(
            leave.user_slack_id,
            f"{emoji} Your leave request "
            f"({leave.start_date.strftime('%d %b %Y')} – {leave.end_date.strftime('%d %b %Y')}) "
            f"has been *{body.status}* by HR.",
        )
    except Exception:
        logger.warning("Could not notify employee of leave status update", extra={"leave_id": leave_id})

    return LeaveRequestOut.model_validate(leave)