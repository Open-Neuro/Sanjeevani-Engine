@echo off
echo ─────────────────────────────────────────────────────────────────────────────
echo SanjeevaniRxAI — Local Docker Build & Test
echo ─────────────────────────────────────────────────────────────────────────────

:: 1. Build the image
echo [1/2] Building "sanjeevani-backend" image...
docker build -t sanjeevani-backend .

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Docker build failed. Make sure Docker Desktop is running.
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Build successful! 
echo.
echo To test this image locally with your Atlas database, run:
echo docker run -it --rm -p 8000:8000 -e MONGO_URI="YOUR_ATLAS_URI_HERE" sanjeevani-backend
echo.
echo ─────────────────────────────────────────────────────────────────────────────
pause
