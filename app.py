import streamlit as st
import pdfplumber
import csv
import io
import re
from PIL import Image
import pytesseract

# For Streamlit Sharing, tesseract is installed via packages.txt

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
    desc = re.sub(r'\s+', ' ', desc.strip())  # Remove extra spaces
    desc = re.sub(r'\d{4,}|RRN:|Serial:|AcqId:|TranDate:|Value date:|Reference:', '', desc)  # Remove long numbers/codes
    desc = re.sub(r'#|# #', '', desc)  # Remove ##
    return desc

def extract_and_parse_pdf(pdf_file):
    transactions = []
    text = ''
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + '\n'
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    transactions += process_table(table)
            if not page_text or not tables:  # OCR if no text/tables
                img = page.to_image(resolution=300)
                ocr_text = pytesseract.image_to_string(img.original)
                text += ocr_text + '\n'
    if not transactions and text:  # Fallback to line-based if no tables
        transactions = fallback_line_parser(text)
    return transactions

def process_table(table):
    trans = []
    if len(table) < 2:
        return trans
    headers = [str(h).lower() if h else '' for h in table[0]]
    date_col = next((i for i, h in enumerate(headers) if 'date' in h), None)
    desc_col = next((i for i, h in enumerate(headers) if 'detail' in h or 'descrip' in h or 'particular' in h), 0)
    debit_col = next((i for i, h in enumerate(headers) if 'debit' in h), None)
    credit_col = next((i for i, h in enumerate(headers) if 'credit' in h), None)
    amount_col = next((i for i, h in enumerate(headers) if 'amount' in h or 'transaction amount' in h), None)
    if date_col is None:
        return trans
    for row in table[1:]:
        row = [str(r) if r else '' for r in row]
        if not any(row):
            continue
        date = row[date_col].strip()
        desc = row[desc_col].strip()
        if not date or not desc:
            continue
        if amount_col is not None:
            amount_str = row[amount_col].replace(' ', '').strip()
            sign = -1 if amount_str.endswith('-') or 'Dr' in amount_str else 1
            amount_str = re.sub(r'[^\d.]', '', amount_str)  # Remove non-numbers
            try:
                amount = float(amount_str) * sign
            except ValueError:
                continue
        elif debit_col is not None and credit_col is not None:
            debit_str = row[debit_col].replace(',', '').replace(' ', '').strip() or '0'
            credit_str = row[credit_col].replace(',', '').replace(' ', '').strip() or '0'
            try:
                amount = float(credit_str) - float(debit_str)
            except ValueError:
                continue
        else:
            continue
        clean_desc = clean_description(desc)
        trans.append((date, clean_desc, amount))
    return trans

def fallback_line_parser(text):
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    trans = []
    desc_lines = []
    current_date = ''
    current_amount = 0
    for line in lines:
        # Detect date like '02 17' or 'Apr 02, 2024'
        if re.match(r'^\d{2} \d{2}$', line) or re.match(r'^[A-Za-z]{3} \d{2}, \d{4}$', line):
            if desc_lines:
                desc = clean_description(' '.join(desc_lines))
                trans.append((current_date, desc, current_amount))
            current_date = line
            desc_lines = []
        # Detect amount like '1,500.00-' or '200,000.00'
        elif re.match(r'^[\d,]+\.\d{2}[-]?$$', line):
            sign = -1 if line.endswith('-') else 1
            amount_str = re.sub(r'[^\d.]', '', line)
            current_amount = float(amount_str) * sign
        # Ignore balance (has comma and .09 like '115,633.09')
        elif re.match(r'^\d{1,3}(?:,\d{3})*\.\d{2}$', line):
            continue
        else:
            desc_lines.append(line)
    if desc_lines:
        desc = clean_description(' '.join(desc_lines))
        trans.append((current_date, desc, current_amount))
    return trans

st.title("Bank Statement PDF to CSV Converter")
st.write("Upload your South African bank statement PDF (supports Standard Bank, FNB, HBZ, ABSA, scanned/normal). Get a clean CSV for Xero.")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    with st.spinner("Processing PDF... (may take time for scanned files)"):
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
        st.error("No transactions found. Ensure it's a valid bank statement or try another file.")
