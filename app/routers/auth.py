from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.schemas import Token, UserCreate
from app.security import create_access_token, hash_password, verify_password

router = APIRouter()


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(
    body: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Token:
    email = body.email.lower().strip()
    exists = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")
    try:
        hashed = hash_password(body.password)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"비밀번호 암호화 중 오류가 발생했습니다: {exc}") from exc

    user = User(email=email, hashed_password=hashed)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # 동시 요청 race condition으로 unique 충돌 시 사용자 친화 메시지 반환
        raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"회원가입 저장 중 오류가 발생했습니다: {exc}") from exc
    db.refresh(user)
    token = create_access_token(str(user.id), settings)
    return Token(access_token=token)


@router.post("/token", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Token:
    email = form.username.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return Token(access_token=create_access_token(str(user.id), settings))
