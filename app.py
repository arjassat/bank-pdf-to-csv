import streamlit as st
import pdfplumber
import csv
import io
import re
from PIL import Image
import pytesseract
import fitz  # PyMuPDF
import numpy as np

# Preprocess image for better OCR
def preprocess_image(image):
    img = image.convert('L')
    img = np.array(img)
    img = (255 - img)  # Invert if needed
    return Image.fromarray(img)

def detect_bank(text):
    text_lower = text.lower()
    if 'standard bank' in text_lower:
        return 'standard'
    elif 'fnb' in text_lower or 'first national bank' in text_lower:
        return 'fnb'
    elif 'hbz' in text_lower:
        return 'hbz'
    elif 'absa' in text_lower:
        return 'absa'
    elif 'nedbank' in text_lower:
        return 'nedbank'
    elif 'capitec' in text_lower:
        return 'capitec'
    return 'unknown'

def clean_description(desc):
    desc = re.sub(r'\s+', ' ', desc.strip())
    desc = re.sub(r'\d{4,}|RRN:|Serial:|AcqId:|TranDate:|Value date:|Reference:', '', desc)
    desc = re.sub(r'#|# #', '', desc)
    return desc

def extract_and_parse_pdf(pdf_file):
    transactions = []
    text = ''
    pdf_bytes = pdf_file.read()
    bank = 'unknown'
    try:
        # Use PyMuPDF for robust handling of corrupt PDFs
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text")
            if page_text.strip():
                text += page_text + '\n'
            else:
                # OCR if no text layer
                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                preprocessed_img = preprocess_image(img)
                ocr_text = pytesseract.image_to_string(preprocessed_img)
                text += ocr_text + '\n'
        doc.close()

        bank = detect_bank(text)

        # Try pdfplumber for table extraction (may fail on corrupt PDFs)
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for p in pdf.pages:
                    tables = p.extract_tables()
                    if tables:
                        for table in tables:
                            transactions += process_table(table, bank)
        except Exception as plumbr_err:
            st.warning(f"Table extraction failed: {str(plumbr_err)}. Falling back to text parsing.")

    except Exception as e:
        st.error(f"Failed to open PDF: {str(e)}. The file may be corrupted. Try uploading a repaired version.")
        return []

    if not transactions and text:
        transactions = fallback_line_parser(text, bank)

    return transactions

# Rest of the code (process_table, fallback_line_parser, st UI) remains the same as your previous version.

st.title("Bank Statement PDF to CSV Converter")
st.write("Upload your South African bank statement PDF (supports Standard Bank, FNB, HBZ, ABSA, scanned/normal/corrupt). Get a clean CSV for Xero.")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    with st.spinner("Processing PDF... (may take time for scanned or corrupt files)"):
        transactions = extract_and_parse_pdf(uploaded_file)
    if transactions:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "description", "amount"])
        for t in transactions:
            writer.writerow(t)
        st.success(f"Extracted {len(transactions)} transactions!")
        st.download_button("Download CSV", output.getvalue(), "bank_transactions.csv", "text/csv")
    else:
        st.error("No transactions found. Ensure it's a valid bank statement or try another file. If the PDF is corrupt, repair it using tools like MuPDF's 'mutool clean'.")
