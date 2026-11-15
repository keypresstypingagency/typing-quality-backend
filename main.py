
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from docx import Document
import fitz
import pytesseract
from PIL import Image
import io
import re
import gc

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

    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)

    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))

    for section in doc.sections:
        for p in section.header.paragraphs:
            if p.text.strip():
                parts.append(p.text)

        for p in section.footer.paragraphs:
            if p.text.strip():
                parts.append(p.text)

    return "\n".join(parts)


def extract_page_text(page):
    return page.get_text("text", sort=True)


def ocr_page(page):
    pix = page.get_pixmap(
        matrix=fitz.Matrix(1.5, 1.5),
        colorspace=fitz.csGRAY,
        alpha=False
    )

    image = Image.open(io.BytesIO(pix.tobytes("png")))

    return pytesseract.image_to_string(
        image,
        lang="ben+eng",
        config="--psm 3"
    )


def suspicious_text(text):
    if not text or len(text.strip()) < 10:
        return True

    if "\ufffd" in text:
        return True

    bengali = len(re.findall(r"[\u0980-\u09FF]", text))
    letters = len(re.findall(r"[A-Za-z\u0980-\u09FF]", text))

    if letters > 30 and bengali / letters < 0.15:
        return True

    if re.search(r"(.)\1{8,}", text):
        return True

    return False


def sample_pages(total):
    indexes = {0, total // 2, total - 1}
    return sorted(indexes)


def extract_pdf_smart(data):
    pdf = fitz.open(stream=data, filetype="pdf")
    total = len(pdf)

    if total == 0:
        return "", "Empty PDF"

    page_texts = {}
    bad_pages = set()

    # First pass: fast native extraction of every page
    for i in range(total):
        page_text = extract_page_text(pdf[i])
        page_texts[i] = page_text

        if suspicious_text(page_text):
            bad_pages.add(i)

    # Check a few sample pages with OCR
    samples = sample_pages(total)
    global_bad = False

    for i in samples:
        native = page_texts[i]
        ocr = ocr_page(pdf[i])

        if suspicious_text(native) and not suspicious_text(ocr):
            bad_pages.add(i)

        elif (
            len(ocr.strip()) > 20
            and len(native.strip()) > 20
            and len(ocr.strip()) > len(native.strip()) * 0.5
        ):
            native_bangla = len(re.findall(r"[\u0980-\u09FF]", native))
            ocr_bangla = len(re.findall(r"[\u0980-\u09FF]", ocr))

            if native_bangla == 0 and ocr_bangla > 10:
                global_bad = True

        del ocr
        gc.collect()

    # If samples suggest broken font mapping, OCR all pages
    if global_bad:
        bad_pages = set(range(total))

    # OCR only selected pages
    for i in sorted(bad_pages):
        ocr = ocr_page(pdf[i])

        if len(ocr.strip()) > len(page_texts[i].strip()):
            page_texts[i] = ocr

        gc.collect()

    result = "\n\n".join(
        f"--- Page {i + 1} ---\n{page_texts[i]}"
        for i in range(total)
    )

    method = (
        "Smart OCR - all pages"
        if global_bad
        else f"Smart OCR - {len(bad_pages)} pages"
    )

    pdf.close()
    return result, method


@app.post("/extract")
async def extract_file(file: UploadFile = File(...)):
    data = await file.read()

    if not data:
        raise HTTPException(400, "Empty file")

    filename = file.filename.lower()

    try:
        if filename.endswith(".docx"):
            text = extract_docx(data)
            method = "DOCX Extraction"

        elif filename.endswith(".pdf"):
            text, method = extract_pdf_smart(data)

        elif filename.endswith(".txt"):
            text = data.decode("utf-8", errors="replace")
            method = "TXT Extraction"

        else:
            raise HTTPException(
                400,
                "Only PDF, DOCX and TXT files are supported"
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
        raise HTTPException(500, str(e))


@app.post("/pdf-fonts")
async def pdf_fonts(file: UploadFile = File(...)):
    data = await file.read()
    pdf = fitz.open(stream=data, filetype="pdf")

    fonts = []

    for page_no, page in enumerate(pdf):
        for font in page.get_fonts(full=True):
            fonts.append({
                "page": page_no + 1,
                "font": font[3],
                "type": font[1],
                "encoding": font[2]
            })

    pdf.close()
    return {"fonts": fonts}


@app.post("/pdf-debug")
async def pdf_debug(file: UploadFile = File(...)):
    data = await file.read()
    pdf = fitz.open(stream=data, filetype="pdf")

    result = []

    for i, page in enumerate(pdf):
        result.append({
            "page": i + 1,
            "text": page.get_text("text")[:2000]
        })

    pdf.close()
    return {"pages": result}


@app.post("/ocr-test")
async def ocr_test(file: UploadFile = File(...)):
    data = await file.read()
    pdf = fitz.open(stream=data, filetype="pdf")

    results = []

    for i, page in enumerate(pdf):
        results.append({
            "page": i + 1,
            "text": ocr_page(page)
        })

    pdf.close()
    return {
        "filename": file.filename,
        "pages": len(results),
        "ocr_text": results
    }

@app.post("/compare")
async def compare_files(
    original_file: UploadFile = File(...),
    typed_file: UploadFile = File(...)
):
    try:
        original_data = await original_file.read()
        typed_data = await typed_file.read()

        original_text = extract_docx(original_data)
        typed_text = extract_docx(typed_data)

        def tokenize(text):
            return re.findall(r"\S+", text)

        from difflib import SequenceMatcher

        original_words = tokenize(original_text)
        typed_words = tokenize(typed_text)

        matcher = SequenceMatcher(
            None,
            original_words,
            typed_words,
            autojunk=False
        )

        missing = []
        extra = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("delete", "replace"):
                missing.extend(original_words[i1:i2])

            if tag in ("insert", "replace"):
                extra.extend(typed_words[j1:j2])

        return {
            "original_words": len(original_words),
            "typed_words": len(typed_words),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "missing_words": missing,
            "extra_words": extra
        }

    except Exception as e:
        raise HTTPException(500, str(e))
