"""
app/api/auth.py
─────────────────────────────────────────────────────────────────────────────
Google OAuth 2.0 authentication endpoints for SanjeevaniRxAI.

Features
────────
• Google OAuth login flow
• JWT token generation and validation
• User session management
• Protected route decorator

Endpoints
─────────
• GET  /auth/login          - Initiate Google OAuth flow
• GET  /auth/callback       - Handle OAuth callback
• POST /auth/logout         - Logout user
• GET  /auth/me             - Get current user info
"""

from __future__ import annotations

import jwt
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from app.config import settings
from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer()

# ─────────────────────────────────────────────────────────────────────────────
# Google OAuth Configuration
# ─────────────────────────────────────────────────────────────────────────────

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def get_google_flow() -> Flow:
    """Create Google OAuth flow instance."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow


# ─────────────────────────────────────────────────────────────────────────────
# JWT Token Management
# ─────────────────────────────────────────────────────────────────────────────


def create_jwt_token(user_data: dict) -> str:
    """Generate JWT token for authenticated user."""
    expiration = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    payload = {
        "sub": user_data["email"],
        "email": user_data["email"],
        "name": user_data.get("name", ""),
        "picture": user_data.get("picture", ""),
        "exp": expiration,
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token


def verify_jwt_token(token: str) -> dict:
    """Verify and decode JWT token."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dependency: Get Current User
# ─────────────────────────────────────────────────────────────────────────────


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency to extract and validate the current user from JWT token.
    
    Usage in protected routes:
        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"user": user}
    """
    token = credentials.credentials
    user_data = verify_jwt_token(token)
    return user_data


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/login", summary="Initiate Google OAuth login")
async def login(request: Request):
    """
    Redirect user to Google OAuth consent screen.
    
    Returns:
        RedirectResponse to Google OAuth URL
    """
    try:
        flow = get_google_flow()
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account",
        )
        
        logger.info("OAuth login initiated", extra={"state": state})
        return RedirectResponse(url=authorization_url)
    
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate login: {str(e)}",
        )


@router.get("/callback", summary="Handle Google OAuth callback")
async def callback(request: Request):
    """
    Handle OAuth callback from Google.
    
    Query Parameters:
        code: Authorization code from Google
        state: State parameter for CSRF protection
    
    Returns:
        JWT token and user information
    """
    try:
        # Get authorization code from query params
        code = request.query_params.get("code")
        if not code:
            raise HTTPException(
                status_code=400,
                detail="Authorization code not provided",
            )
        
        # Exchange code for tokens
        flow = get_google_flow()
        flow.fetch_token(code=code)
        
        # Get user info from Google
        credentials = flow.credentials
        user_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
        
        # Extract user data
        user_data = {
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture"),
            "google_id": user_info.get("sub"),
        }
        
        # Store/update user in database
        db = get_db()
        users_collection = db["users"]
        
        users_collection.update_one(
            {"email": user_data["email"]},
            {
                "$set": {
                    **user_data,
                    "last_login": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
                "$setOnInsert": {"created_at": datetime.utcnow()},
            },
            upsert=True,
        )
        
        # Generate JWT token
        jwt_token = create_jwt_token(user_data)
        
        logger.info(
            "User authenticated successfully",
            extra={"email": user_data["email"]},
        )
        
        # Redirect to the frontend with token
        # Note: We use /callback path so the frontend popup polling logic can intercept it.
        redirect_url = f"{settings.FRONTEND_URL}/callback?token={jwt_token}"
        return RedirectResponse(url=redirect_url)
    
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Authentication failed: {str(e)}",
        )


@router.get("/me", summary="Get current user information")
async def get_me(user: dict = Depends(get_current_user)):
    """
    Get information about the currently authenticated user.
    
    Requires:
        Authorization: Bearer <jwt_token>
    
    Returns:
        User information from JWT token
    """
    return {
        "status": "success",
        "user": {
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
        },
    }


@router.post("/logout", summary="Logout user")
async def logout(user: dict = Depends(get_current_user)):
    """
    Logout the current user.
    
    Note: Since we're using stateless JWT, this is mainly for client-side
    token removal. In production, consider implementing token blacklisting.
    
    Requires:
        Authorization: Bearer <jwt_token>
    """
    logger.info("User logged out", extra={"email": user.get("email")})
    return {
        "status": "success",
        "message": "Logged out successfully",
    }


@router.get("/health", summary="Auth service health check")
async def auth_health():
    """Check if Google OAuth is properly configured."""
    is_configured = bool(
        settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
    )
    return {
        "status": "ok" if is_configured else "not_configured",
        "google_oauth": is_configured,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
    }
