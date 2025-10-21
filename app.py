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

def process_table(table, bank):
    trans = []
    if len(table) < 2:
        return trans
    headers = [str(h).lower() if h else '' for h in table[0]]
    date_col = next((i for i, h in enumerate(headers) if 'date' in h), None)
    desc_col = next((i for i, h in enumerate(headers) if 'detail' in h or 'descrip' in h or 'particular' in h or 'history' in h), 0)
    amount_col = next((i for i, h in enumerate(headers) if 'amount' in h or 'transaction amount' in h), None)
    debit_col = next((i for i, h in enumerate(headers) if 'debit' in h), None)
    credit_col = next((i for i, h in enumerate(headers) if 'credit' in h), None)
    if date_col is None:
        return trans
    for row in table[1:]:
        row = [str(r) if r else '' for r in row]
        if not any(row):
            continue
        date = row[date_col].strip() if date_col < len(row) else ''
        desc = row[desc_col].strip() if desc_col < len(row) else ''
        if not date or not desc:
            continue
        if bank == 'fnb' or bank == 'absa':
            if amount_col is not None and amount_col < len(row):
                amount_str = row[amount_col].replace(' ', '').strip()
                sign = -1 if amount_str.endswith('-') or 'Dr' in amount_str or 'Dr' in row[amount_col] else 1
                if 'Cr' in amount_str:
                    sign = 1
                amount_str = re.sub(r'[^\d.]', '', amount_str)
                try:
                    amount = float(amount_str) * sign
                except ValueError:
                    continue
            elif debit_col is not None and credit_col is not None and debit_col < len(row) and credit_col < len(row):
                debit_str = row[debit_col].replace(',', '').replace(' ', '').strip() or '0'
                credit_str = row[credit_col].replace(',', '').replace(' ', '').strip() or '0'
                try:
                    amount = float(credit_str) - float(debit_str)
                except ValueError:
                    continue
            else:
                continue
        elif bank == 'standard':
            if debit_col is not None and debit_col < len(row):
                debit_str = row[debit_col].replace(',', '').replace(' ', '').strip() or '0'
                try:
                    amount = -float(debit_str) if debit_str else 0
                except ValueError:
                    continue
            elif credit_col is not None and credit_col < len(row):
                credit_str = row[credit_col].replace(',', '').replace(' ', '').strip() or '0'
                try:
                    amount = float(credit_str) if credit_str else 0
                except ValueError:
                    continue
            else:
                continue
        elif bank == 'hbz':
            if debit_col is not None and debit_col < len(row):
                debit_str = row[debit_col].replace(',', '').strip() or '0'
                try:
                    amount = -float(debit_str) if debit_str else 0
                except ValueError:
                    continue
            elif credit_col is not None and credit_col < len(row):
                credit_str = row[credit_col].replace(',', '').strip() or '0'
                try:
                    amount = float(credit_str) if credit_str else 0
                except ValueError:
                    continue
            else:
                continue
        else:
            continue
        clean_desc = clean_description(desc)
        trans.append((date, clean_desc, amount))
    return trans

def fallback_line_parser(text, bank):
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    trans = []
    current_date = ''
    current_desc = []
    current_amount = 0.0
    in_transaction_history = False
    for line in lines:
        if 'transaction history' in line.lower() or 'details' in line.lower() or 'particulars' in line.lower() or 'transactions' in line.lower():
            in_transaction_history = True
            continue
        if not in_transaction_history:
            continue
        if bank == 'absa':
            line_match = match(r'(\d{1,2} [A-Za-z]{3} \d{4})\s+(.*?)\s+([\d ,]+\.\d{2}-?)\s*([\d ,]+\.\d{2})?$', line)
            if line_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_date = line_match.group(1)
                current_desc = [line_match.group(2)]
                amount_str = line_match.group(3).replace(' ', '').replace(',', '')
                sign = -1 if amount_str.endswith('-') else 1
                current_amount = float(amount_str.rstrip('-')) * sign
                if 'balance brought forward' in ' '.join(current_desc).lower():
                    current_amount = 0.0
                continue
            fee_match = match(r'Service Fee\s+([\d ,]+\.\d{2}-?)\s*([\d ,]+\.\d{2})?$', line)
            if fee_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_desc = ['Service Fee']
                amount_str = fee_match.group(1).replace(' ', '').replace(',', '')
                sign = -1 if amount_str.endswith('-') else 1
                current_amount = float(amount_str.rstrip('-')) * sign
                continue
            if 'balance' in line.lower():
                continue
            if current_date:
                current_desc.append(line)
        elif bank == 'standard':
            line_match = match(r'(.*?)\s*(##)?\s*([\d,]+\.\d{2}-?)\s*(\d{2} \d{2})\s*([\d,]+\.\d{2})', line)
            if line_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_desc = [line_match.group(1)]
                amount_str = line_match.group(3).replace(',', '')
                sign = -1 if amount_str.endswith('-') else 1
                current_amount = float(amount_str.rstrip('-')) * sign
                current_date = line_match.group(4)
                if 'balance brought forward' in ' '.join(current_desc).lower():
                    current_amount = 0.0
                continue
            if 'balance' in line.lower():
                continue
            if current_date:
                current_desc.append(line)
        elif bank == 'fnb':
            line_match = match(r'(\d{2} [A-Za-z]{3})\s+(.*?)\s+([\d,]+\.\d{2}(Cr)?)\s+([\d,]+\.\d{2}(Cr)?)', line)
            if line_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_date = line_match.group(1)
                current_desc = [line_match.group(2)]
                amount_str = line_match.group(3).rstrip('Cr').replace(',', '')
                sign = 1 if 'Cr' in line_match.group(3) else -1
                current_amount = float(amount_str) * sign
                continue
            if 'balance' in line.lower():
                continue
            if current_date:
                current_desc.append(line)
        elif bank == 'hbz':
            line_match = match(r'([A-Za-z]{3} \d{2}, \d{4})\s+(.*?)(\s+[\d,]+\.\d{2})?(\s+[\d,]+\.\d{2})?$', line)
            if line_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_date = line_match.group(1)
                current_desc = [line_match.group(2)]
                if line_match.group(3):
                    amount_str = line_match.group(3).strip().replace(',', '')
                    current_amount = -float(amount_str)
                elif line_match.group(4):
                    amount_str = line_match.group(4).strip().replace(',', '')
                    current_amount = float(amount_str)
                else:
                    current_amount = 0.0
                continue
            ref_match = match(r'Reference: (.*?)(\s+[\d,]+\.\d{2})?$', line)
            if ref_match and current_date:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_desc = ['Reference: ' + ref_match.group(1)]
                if ref_match.group(2):
                    amount_str = ref_match.group(2).strip().replace(',', '')
                    current_amount = -float(amount_str)
                else:
                    current_amount = 0.0
                continue
            if 'balance' in line.lower():
                continue
            if current_date:
                current_desc.append(line)
                if 'EFT' in line:
                    current_amount = -current_amount
        else:
            date_match = match(r'^(\d{2} \d{2})', line)
            if date_match:
                if current_desc:
                    desc = clean_description(' '.join(current_desc))
                    trans.append((current_date, desc, current_amount))
                current_date = date_match.group(1)
                rest = line[date_match.end():]
                amount_match = search(r'([\d,]+\.\d{2}-?)$', rest)
                if amount_match:
                    desc = rest[:amount_match.start()].strip()
                    amount_str = amount_match.group(1).replace(',', '')
                    sign = -1 if amount_str.endswith('-') else 1
                    current_amount = float(amount_str.rstrip('-')) * sign
                    current_desc = [desc]
                else:
                    current_desc = [rest.strip()]
            elif current_date:
                amount_match = search(r'([\d,]+\.\d{2}-?)$', line)
                if amount_match:
                    desc = line[:amount_match.start()].strip()
                    amount_str = amount_match.group(1).replace(',', '')
                    sign = -1 if amount_str.endswith('-') else 1
                    current_amount = float(amount_str.rstrip('-')) * sign
                    current_desc.append(desc)
                else:
                    current_desc.append(line)
    if current_desc:
        desc = clean_description(' '.join(current_desc))
        trans.append((current_date, desc, current_amount))
    return trans

st.title("Bank Statement PDF to CSV Converter")
st.write("Upload your South African bank statement PDF (supports Standard Bank, FNB, HBZ, ABSA, scanned/normal). Get a clean CSV for Xero.")

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
