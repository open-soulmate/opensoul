from fastapi import APIRouter, HTTPException

from src.models.user import UserCreate, UserResponse, Token
from src.services.auth import authenticate_user, register_user, create_access_token

router = APIRouter()


@router.post("/register", response_model=UserResponse)
async def register(data: UserCreate):
    try:
        user = await register_user(data.username, data.email, data.password)
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login", response_model=Token)
async def login(username: str, password: str):
    user = await authenticate_user(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer"}
