
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from docx import Document
from fontTools.ttLib import TTFont
from PIL import Image
import pytesseract
import fitz
import io
import re

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


# DOCX TEXT EXTRACTION
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


# PDF TEXT EXTRACTION
def extract_pdf(data):
    pdf = fitz.open(stream=data, filetype="pdf")
    pages = []

    for page in pdf:
        pages.append(page.get_text("text"))

    return "\n".join(pages)


# PDF OCR EXTRACTION
def extract_pdf_ocr(data):
    pdf = fitz.open(stream=data, filetype="pdf")
    pages = []

    for page in pdf:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(2, 2),
            alpha=False
        )

        image = Image.open(
            io.BytesIO(pix.tobytes("png"))
        )

        text = pytesseract.image_to_string(
            image,
            lang="ben+eng"
        )

        pages.append(text)

    return "\n".join(pages)


# CHECK WHETHER PDF TEXT MAY BE CORRUPTED
def is_suspicious_text(text):
    if not text.strip():
        return True

    if "\ufffd" in text:
        return True

    # Repeated Bengali vowel signs can indicate extraction problems
    repeated_marks = re.search(
        r"([\u09BE-\u09CC])\1",
        text
    )

    if repeated_marks:
        return True

    # Too few Bengali characters in a Bengali-looking document
    bengali_count = len(
        re.findall(r"[\u0980-\u09FF]", text)
    )

    if len(text.strip()) > 30 and bengali_count == 0:
        return True

    return False


# MAIN FILE EXTRACTION WITH AUTOMATIC OCR FALLBACK
@app.post("/extract")
async def extract_file(file: UploadFile = File(...)):
    data = await file.read()
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".docx"):
            text = extract_docx(data)
            method = "DOCX Text Extraction"

        elif filename.endswith(".pdf"):
            text = extract_pdf(data)
            method = "PDF Text Extraction"

            # Run OCR if extracted PDF text looks suspicious
            if is_suspicious_text(text):
                ocr_text = extract_pdf_ocr(data)

                if len(ocr_text.strip()) > 0:
                    text = ocr_text
                    method = "Bengali OCR"

        else:
            raise HTTPException(
                status_code=400,
                detail="Only PDF and DOCX files are supported"
            )

        return {
            "filename": file.filename,
            "characters": len(text),
            "method": method,
            "text": text
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract text: {str(e)}"
        )


# PDF FONT CHECK
@app.post("/pdf-fonts")
async def check_pdf_fonts(file: UploadFile = File(...)):
    data = await file.read()

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            400,
            "Please upload a PDF file"
        )

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


# PDF DEBUG
@app.post("/pdf-debug")
async def debug_pdf(file: UploadFile = File(...)):
    data = await file.read()

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            400,
            "Please upload a PDF file"
        )

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        for page_number, page in enumerate(pdf, start=1):
            page_data = {
                "page": page_number,
                "extracted_text": page.get_text("text"),
                "text_spans": []
            }

            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if "lines" not in block:
                    continue

                for line in block["lines"]:
                    for span in line["spans"]:
                        page_data["text_spans"].append({
                            "text": span["text"],
                            "font": span["font"],
                            "size": span["size"],
                            "bbox": list(span["bbox"])
                        })

            results.append(page_data)

        return {
            "filename": file.filename,
            "pages": len(results),
            "debug": results
        }

    except Exception as e:
        raise HTTPException(400, str(e))


# SEPARATE OCR TEST
@app.post("/ocr-test")
async def ocr_test(file: UploadFile = File(...)):
    data = await file.read()

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            400,
            "Please upload a PDF file"
        )

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        for page_number, page in enumerate(pdf, start=1):
            pix = page.get_pixmap(
                matrix=fitz.Matrix(2, 2),
                alpha=False
            )

            image = Image.open(
                io.BytesIO(pix.tobytes("png"))
            )

            text = pytesseract.image_to_string(
                image,
                lang="ben+eng"
            )

            results.append({
                "page": page_number,
                "text": text
            })

        return {
            "filename": file.filename,
            "pages": len(results),
            "ocr_text": results
        }

    except Exception as e:
        raise HTTPException(
            400,
            f"OCR failed: {str(e)}"
        )
