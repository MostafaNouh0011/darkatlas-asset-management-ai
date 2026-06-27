"""
Lightweight API key auth on write operations.

This satisfies the "writes require authentication" requirement without pulling
in a full OAuth/JWT setup, which would be disproportionate for an internal
API of this scope. This choice is documented in the README as a deliberate
scope decision.
"""
import os
from fastapi import Header, HTTPException, status

API_KEY = os.getenv("API_KEY", "change-me-to-a-real-secret")


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Send it in the X-API-Key header.",
        )
