from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os
import shutil
import tempfile
from datetime import date
from index import extract_attendance
from optimizer import optimize_waivers

app = FastAPI(title="Waiver Optimizer API")

# Mount the static files directory
# Place index.html, style.css, and app.js in the 'static' folder
app.mount("/ui", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def redirect_to_ui():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui")

@app.post("/api/optimize")
async def handle_optimization(
    file: UploadFile = File(...),
    num_waivers: int = Form(...),
    start_month: int = Form(8),
    priorities: str = Form(None)
):
    temp_path = None
    try:
        # 1. Create a manual temporary file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"optim_upload_{file.filename}")
        
        # 2. Save the uploaded content
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 3. Extract attendance data
        anchor_date = date(2026, start_month, 2)
        df, records = extract_attendance(temp_path, start_date=anchor_date)
        
        # 4. Parse priorities if provided
        priority_map = {}
        if priorities:
            try:
                import json
                priority_map = json.loads(priorities)
            except:
                pass
        
        # 5. Optimize waivers
        results = optimize_waivers(records, num_waivers=num_waivers, priorities=priority_map)
        
        return results

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Ensure we return a structured error response
        return JSONResponse(status_code=500, content={"error": str(e)})
    
    finally:
        # 5. Guaranteed Cleanup
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as cleanup_err:
                print(f"Warning: Failed to cleanup temp file {temp_path}: {cleanup_err}")

if __name__ == "__main__":
    import uvicorn
    # Use environment port for deployment, default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
