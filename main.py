from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Typing Quality Checker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Typing Quality Checker Backend is running!"
    }

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()

    return {
        "filename": file.filename,
        "size": len(content),
        "message": "File received successfully"
    }
