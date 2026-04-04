from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.utils.security import get_current_user
from app.modules.safety_validation import SafetyValidationService

router = APIRouter(prefix="/chat", tags=["Chat"])
_safety = SafetyValidationService()

class MatchedMedicine(BaseModel):
    name: str
    dosage: str
    confidence: float
    in_database: bool

class PrescriptionUploadResponse(BaseModel):
    matched_medicines: List[MatchedMedicine]
    unmatched_medicines: List[str]
    required_next_fields: List[str]

@router.post("/upload-prescription", response_model=PrescriptionUploadResponse)
async def upload_prescription(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user)
):
    """
    Mock OCR endpoint: Parses uploaded prescription and matches against database.
    """
    contents = await file.read()
    
    # Simulate OCR failure condition
    if not contents or "error" in file.filename.lower():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "OCR failed to read the image",
                "prompt": "Please try capturing a sharper image of the prescription in good lighting."
            }
        )
        
    merchant_id = user["merchant_id"]
    
    # Mock extracted items
    mock_extracted = [
        {"name": "Metformin 500mg", "dosage": "500mg", "confidence": 0.98},
        {"name": "Atorvastatin 20mg", "dosage": "20mg", "confidence": 0.95},
        {"name": "UnknownMedicineXYZ", "dosage": "10mg", "confidence": 0.45}
    ]
    
    matched, unmatched = _safety.match_extracted_medicines(mock_extracted, merchant_id)
    
    return {
        "matched_medicines": matched,
        "unmatched_medicines": unmatched,
        # Requirements strictly ask to include these fields:
        "required_next_fields": ["quantity_confirmation", "delivery_address"]
    }
