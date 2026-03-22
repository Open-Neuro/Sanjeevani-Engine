import jwt
from datetime import datetime
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
security = HTTPBearer()

def verify_jwt_token(token: str) -> dict:
    """Verify and decode JWT token using shared secret."""
    try:
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET, 
            algorithms=[settings.JWT_ALGORITHM]
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

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency to extract and validate the current user from JWT token.
    Safe to use across all Sanjeevani System routes.
    """
    token = credentials.credentials
    user_data = verify_jwt_token(token)
    
    # Ensure merchant_id is present for multi-tenancy filtering
    if "merchant_id" not in user_data:
        user_data["merchant_id"] = user_data.get("email")
        
    return user_data
