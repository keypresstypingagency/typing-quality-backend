
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from docx import Document
import fitz
import io
import os

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

def extract_docx(data):
    doc = Document(io.BytesIO(data))
    parts = []

    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)

    for section in doc.sections:
        for paragraph in section.header.paragraphs:
            if paragraph.text.strip():
                parts.append(paragraph.text)

        for paragraph in section.footer.paragraphs:
            if paragraph.text.strip():
                parts.append(paragraph.text)

    return "\n".join(parts)

def extract_pdf(data):
    pdf = fitz.open(stream=data, filetype="pdf")
    pages = []

    for page in pdf:
        pages.append(page.get_text("text"))

    return "\n".join(pages)

@app.post("/extract")
async def extract_file(file: UploadFile = File(...)):
    data = await file.read()
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".docx"):
            text = extract_docx(data)

        elif filename.endswith(".pdf"):
            text = extract_pdf(data)

        else:
            raise HTTPException(
                status_code=400,
                detail="Only PDF and DOCX files are supported"
            )

        return {
            "filename": file.filename,
            "characters": len(text),
            "text": text
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract text: {str(e)}"
        )

from fontTools.ttLib import TTFont

@app.post("/pdf-fonts")
async def check_pdf_fonts(file: UploadFile = File(...)):
    data = await file.read()

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file")

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        for page_number, page in enumerate(pdf, start=1):
            for font in page.get_fonts(full=True):
                xref = font[0]

                item = {
                    "page": page_number,
                    "font_name": font[3],
                    "font_type": font[2],
                    "encoding": font[5],
                    "unicode_cmap": "Unknown"
                }

                try:
                    extracted = pdf.extract_font(xref)
                    font_data = extracted[3]

                    if font_data:
                        font_obj = TTFont(
                            io.BytesIO(font_data),
                            lazy=True
                        )
                        item["unicode_cmap"] = any(
                            table.isUnicode()
                            for table in font_obj["cmap"].tables
                        )
                        font_obj.close()

                except Exception:
                    item["unicode_cmap"] = "Could not inspect"

                results.append(item)

        return {
            "filename": file.filename,
            "fonts_found": len(results),
            "fonts": results
        }

    except Exception as e:
        raise HTTPException(400, str(e))
