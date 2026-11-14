
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from docx import Document
from fontTools.ttLib import TTFont
from PIL import Image
import pytesseract
import fitz
import io
import re
import unicodedata

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
    try:
        return "\n".join(
            page.get_text("text") for page in pdf
        )
    finally:
        pdf.close()


# PDF OCR EXTRACTION
def extract_pdf_ocr(data):
    pdf = fitz.open(stream=data, filetype="pdf")
    pages = []

    try:
        for page in pdf:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(2, 2),
                alpha=False
            )

            image = Image.open(
                io.BytesIO(pix.tobytes("png"))
            )

            try:
                text = pytesseract.image_to_string(
                    image,
                    lang="ben+eng"
                )
                pages.append(text)
            finally:
                image.close()

        return "\n".join(pages)

    finally:
        pdf.close()


# TEXT QUALITY SCORING
def text_quality_score(text):
    if not text.strip():
        return 0

    visible_chars = [
        char for char in text if not char.isspace()
    ]

    if not visible_chars:
        return 0

    total = len(visible_chars)

    valid_chars = sum(
        1 for char in visible_chars
        if char.isprintable()
        and unicodedata.category(char) not in ("Co", "Cs")
    )

    bengali_count = len(
        re.findall(r"[\u0980-\u09FF]", text)
    )

    alphabetic_count = sum(
        1 for char in text if char.isalpha()
    )

    repeated_marks = len(
        re.findall(r"([\u09BE-\u09CC])\1+", text)
    )

    replacement_count = text.count("\ufffd")

    score = (valid_chars / total) * 100

    if alphabetic_count > 0 and bengali_count > 0:
        bengali_ratio = bengali_count / alphabetic_count
        score += bengali_ratio * 10

    score -= repeated_marks * 8
    score -= replacement_count * 20

    return round(max(0, min(110, score)), 2)


# MAIN FILE EXTRACTION
@app.post("/extract")
async def extract_file(file: UploadFile = File(...)):
    data = await file.read()
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".docx"):
            text = extract_docx(data)

            return {
                "filename": file.filename,
                "characters": len(text),
                "method": "DOCX Text Extraction",
                "text": text
            }

        elif filename.endswith(".pdf"):

            # First: Normal PDF extraction
            pdf_text = extract_pdf(data)

            # Second: OCR extraction
            ocr_text = extract_pdf_ocr(data)

            # Compare both results
            pdf_score = text_quality_score(pdf_text)
            ocr_score = text_quality_score(ocr_text)

            if ocr_score > pdf_score:
                final_text = ocr_text
                method = "Bengali OCR"
            else:
                final_text = pdf_text
                method = "PDF Text Extraction"

            return {
                "filename": file.filename,
                "characters": len(final_text),
                "method": method,
                "quality_scores": {
                    "pdf_text_score": pdf_score,
                    "ocr_score": ocr_score
                },
                "text": final_text
            }

        else:
            raise HTTPException(
                status_code=400,
                detail="Only PDF and DOCX files are supported"
            )

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
        raise HTTPException(400, "Please upload a PDF file")

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        try:
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

        finally:
            pdf.close()

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
        raise HTTPException(400, "Please upload a PDF file")

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        try:
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

        finally:
            pdf.close()

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
        raise HTTPException(400, "Please upload a PDF file")

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        results = []

        try:
            for page_number, page in enumerate(pdf, start=1):
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    alpha=False
                )

                image = Image.open(
                    io.BytesIO(pix.tobytes("png"))
                )

                try:
                    text = pytesseract.image_to_string(
                        image,
                        lang="ben+eng"
                    )
                finally:
                    image.close()

                results.append({
                    "page": page_number,
                    "text": text
                })

        finally:
            pdf.close()

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
