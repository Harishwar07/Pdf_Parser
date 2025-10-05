import pandas as pd
import pdfplumber
import re

import pdfplumber
import pandas as pd
import re

def parse(pdf_path: str) -> pd.DataFrame:
    """
    Parses an ICICI bank statement PDF to extract transaction data.

    Args:
        pdf_path (str): The path to the PDF bank statement file.

    Returns:
        pd.DataFrame: A DataFrame containing the parsed transaction data with columns:
                      ['Date', 'Description', 'Withdrawal', 'Deposit', 'Balance'].
                      Dates are in YYYY-MM-DD format, and numeric columns have NaN for missing values.
    """
    all_transactions_data = []
    # Define the column names as they appear in the PDF for initial extraction
    pdf_header_columns = ['Date', 'Description', 'Debit Amt', 'Credit Amt', 'Balance']
    # Define the target schema columns for the final DataFrame
    target_schema_columns = ['Date', 'Description', 'Withdrawal', 'Deposit', 'Balance']

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract tables from the page using default settings.
            tables = page.extract_tables()

            for table in tables:
                for row in table:
                    # Clean each cell: strip leading/trailing whitespace, replace None with empty string
                    cleaned_row = [cell.strip() if cell is not None else '' for cell in row]

                    # Filter out completely empty rows
                    if not any(cell for cell in cleaned_row):
                        continue

                    # Normalize row for header comparison: replace multiple spaces with single space and strip
                    normalized_row_for_header_check = [re.sub(r'\s+', ' ', cell).strip() for cell in cleaned_row]

                    # Check if the current row is the header row (using the PDF's actual header names)
                    if normalized_row_for_header_check == pdf_header_columns:
                        continue  # Skip header rows

                    # Process as a transaction data row
                    # A valid transaction row is expected to have 5 columns and start with a date in DD-MM-YYYY format.
                    if len(cleaned_row) == 5 and re.match(r'^\d{2}-\d{2}-\d{4}$', cleaned_row[0]):
                        all_transactions_data.append(cleaned_row)
                    else:
                        # If a row does not match the expected 5-column structure or date format,
                        # it's likely a footer, a partial row, or some other non-transaction text.
                        # These rows are skipped to ensure data quality.
                        pass

    # If no transactions were found, return an empty DataFrame with the target schema columns
    if not all_transactions_data:
        return pd.DataFrame(columns=target_schema_columns)

    # Create a DataFrame from the collected transaction data using the PDF's column names
    df = pd.DataFrame(all_transactions_data, columns=pdf_header_columns)

    # --- Data Cleaning and Transformation ---

    # 1. Date conversion: Convert 'DD-MM-YYYY' to 'YYYY-MM-DD'
    # 'errors='coerce'' will turn unparseable dates into NaT (Not a Time)
    df['Date'] = pd.to_datetime(df['Date'], format='%d-%m-%Y', errors='coerce').dt.strftime('%Y-%m-%d')

    # 2. Numeric conversion for 'Debit Amt', 'Credit Amt', 'Balance'
    # Replace empty strings with pandas' NA (Not Available) for better numeric type handling.
    # 'errors='coerce'' will turn non-numeric values into NaN.
    for col in ['Debit Amt', 'Credit Amt', 'Balance']:
        df[col] = df[col].replace('', pd.NA)
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Filter out any rows where the Date could not be parsed, as these are likely malformed
    # or non-transactional rows that slipped through previous filters.
    df.dropna(subset=['Date'], inplace=True)

    # 3. Rename columns to match the target schema: 'Debit Amt' -> 'Withdrawal', 'Credit Amt' -> 'Deposit'
    df.rename(columns={'Debit Amt': 'Withdrawal', 'Credit Amt': 'Deposit'}, inplace=True)

    # 4. Ensure the final DataFrame has the correct column order as per the target schema
    df = df[target_schema_columns]

    return df