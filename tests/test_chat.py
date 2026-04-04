import io
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

class TestChatEndpoints:
    PREFIX = "/api/v1/chat"

    def test_upload_prescription_success(self, client):
        # Create a dummy image file
        file_content = b"fake image content"
        file = {"file": ("prescription.jpg", io.BytesIO(file_content), "image/jpeg")}
        
        # Override dependency if auth is needed via TestClient app overriding
        from app.main import app
        from app.utils.security import get_current_user
        from app.modules.safety_validation import SafetyValidationService

        # Mock the DB call to avoid DNS timeouts during tests
        def mock_match(*args, **kwargs):
            return (
                [{"name": "Metformin 500mg", "dosage": "500mg", "confidence": 0.98, "in_database": True}],
                ["UnknownMedicineXYZ"]
            )
        import app.api.chat
        app.api.chat._safety.match_extracted_medicines = mock_match

        app.dependency_overrides[get_current_user] = lambda: {"merchant_id": "test_merchant"}
        
        try:
            resp = client.post(f"{self.PREFIX}/upload-prescription", files=file)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            
            body = resp.json()
            assert "matched_medicines" in body
            assert "unmatched_medicines" in body
            assert "required_next_fields" in body
            assert "quantity_confirmation" in body["required_next_fields"]
            assert "delivery_address" in body["required_next_fields"]
            
            # Since it's a mock extraction with "Metformin 500mg" and etc., verify the structure
            first_match = body["matched_medicines"][0]
            assert "name" in first_match
            assert "dosage" in first_match
            assert "confidence" in first_match
            assert "in_database" in first_match
            assert first_match["in_database"] is True
            
        finally:
            app.dependency_overrides.clear()

    def test_upload_prescription_ocr_failure(self, client):
        # Simulate failure by naming the file "error.jpg"
        file_content = b"error content"
        file = {"file": ("error_prescription.jpg", io.BytesIO(file_content), "image/jpeg")}
        
        from app.main import app
        from app.utils.security import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"merchant_id": "test_merchant"}

        try:
            resp = client.post(f"{self.PREFIX}/upload-prescription", files=file)
            assert resp.status_code == 400
            
            body = resp.json()
            assert "detail" in body
            assert "error" in body["detail"]
            assert "prompt" in body["detail"]
            assert "retry" in body["detail"]["prompt"].lower() or "try" in body["detail"]["prompt"].lower()
            
        finally:
            app.dependency_overrides.clear()
