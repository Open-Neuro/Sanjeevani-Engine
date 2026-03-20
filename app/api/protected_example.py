"""
app/api/protected_example.py
─────────────────────────────────────────────────────────────────────────────
Example of how to protect your API routes with authentication.

This file demonstrates how to use the get_current_user dependency
to require authentication on your endpoints.
"""

from fastapi import APIRouter, Depends
from app.utils.security import get_current_user

router = APIRouter(prefix="/protected", tags=["Protected Examples"])


@router.get("/dashboard")
async def protected_dashboard(user: dict = Depends(get_current_user)):
    """
    Example protected endpoint - requires valid JWT token.
    
    Usage:
        curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
             http://localhost:8000/api/v1/protected/dashboard
    """
    return {
        "message": f"Welcome to your dashboard, {user['name']}!",
        "user": user,
    }


@router.get("/profile")
async def protected_profile(user: dict = Depends(get_current_user)):
    """
    Another example of a protected endpoint.
    """
    return {
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# How to protect your existing routes
# ─────────────────────────────────────────────────────────────────────────────
"""
To protect any of your existing routes, simply add the dependency:

BEFORE (unprotected):
    @router.get("/customers")
    async def get_customers():
        # ... your code
        pass

AFTER (protected):
    from app.api.auth import get_current_user
    
    @router.get("/customers")
    async def get_customers(user: dict = Depends(get_current_user)):
        # Now only authenticated users can access this
        # You can use user['email'] to filter data, log actions, etc.
        pass

The user dict contains:
    - email: User's email address
    - name: User's full name
    - picture: URL to user's profile picture
    - sub: User's email (subject claim)
    - exp: Token expiration timestamp
    - iat: Token issued at timestamp
"""
