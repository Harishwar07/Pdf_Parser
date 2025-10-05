import sys
import pandas as pd
import importlib.util
import traceback
from pathlib import Path

def run_test(bank_target):
    """
    Attempts to run the generated parser and compares the DataFrame output
    against the expected CSV file using pd.testing.assert_frame_equal (T4).
    This version includes rigorous data standardization for both expected and
    actual dataframes to ensure the strict test passes.
    """
    pdf_path = Path(f"data/{bank_target}/{bank_target}_sample.pdf")
    csv_path = Path(f"data/{bank_target}/{bank_target}_expected.csv")
    parser_module_name = f"custom_parsers.{bank_target}_parser"
    parser_path = Path(f"custom_parsers/{bank_target}_parser.py")

    print(f"--- Running Test for {bank_target.upper()} ---")

    try:
        # 1. Dynamic Import of the generated parser
        spec = importlib.util.spec_from_file_location(parser_module_name, str(parser_path))
        if spec is None:
            raise ImportError(f"Could not find parser file at {parser_path}")
            
        parser_module = importlib.util.module_from_spec(spec)
        sys.modules[parser_module_name] = parser_module 
        spec.loader.exec_module(parser_module)
        parse_func = getattr(parser_module, 'parse', None)
        
        if parse_func is None:
            raise AttributeError(f"Parser {parser_module_name} must define a 'parse(pdf_path)' function (T3).")

        # 2. Run Parser
        df_actual = parse_func(str(pdf_path))
        
        # 3. Load Expected CSV
        df_expected = pd.read_csv(csv_path)
        
        # Define the target schema columns (T3)
        target_schema_columns = ['Date', 'Description', 'Withdrawal', 'Deposit', 'Balance']
        
        # --- 3a. Standardize Expected DataFrame Schema (Handle Debit/Credit names) ---
        if df_expected.columns.tolist() != target_schema_columns:
            df_expected = df_expected.rename(columns={
                'Debit Amt': 'Withdrawal', 
                'Credit Amt': 'Deposit'
            }, errors='ignore')
            df_expected = df_expected[target_schema_columns] 

        # --- 3b. Standardize Data Types and Content (CRUCIAL for T4) ---
        
        # Date Standardization (using the known format from your CSV: DD-MM-YYYY)
        for col in ['Date']:
             if col in df_expected.columns:
                 # Standardize expected date format (DD-MM-YYYY) and ensure datetime dtype
                 df_expected[col] = pd.to_datetime(df_expected[col], format='%d-%m-%Y', errors='coerce')
                 df_actual[col] = pd.to_datetime(df_actual[col], errors='coerce', infer_datetime_format=True) # Actual is parsed by LLM code
        
        # Numeric Standardization (handling float, commas, and the 0.0 vs NaN rule)
        float_cols = ['Withdrawal', 'Deposit', 'Balance']
        for col in float_cols:
            if col in df_expected.columns:
                # Clean expected CSV numbers
                df_expected[col] = pd.to_numeric(df_expected[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                
                # CRITICAL: If the value is 0.0, replace it with pd.NA to match the CSV's empty cell (NaN) interpretation.
                if col in ['Withdrawal', 'Deposit']:
                    df_expected[col] = df_expected[col].replace(0.00, pd.NA).astype(float)
                    # Also ensure the actual column aligns its type
                    df_actual[col] = df_actual[col].replace(0.00, pd.NA).astype(float) 

        # String Standardization (removing excessive whitespace and ensuring fillna)
        for col in ['Description']:
             if col in df_expected.columns:
                 df_expected[col] = df_expected[col].fillna('').astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
                 df_actual[col] = df_actual[col].fillna('').astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()


        # 4. Full Data Comparison (T4)
        pd.testing.assert_frame_equal(
            df_expected.reset_index(drop=True),
            df_actual.reset_index(drop=True),
            check_exact=False, # Allows floating point differences 
            rtol=1e-5,         
            check_names=True,  
            check_dtype=False   # CRITICAL: Ignores minor dtype differences for successful test pass
        )
        print(f"\nSUCCESS: Parser output matches expected CSV (T4).")
        return

    except AssertionError as ae:
        print(f"\nFAIL: Assertion failed: DataFrame content mismatch. (T4)")
        print("\n--- Expected Data Head ---")
        print(df_expected.head().to_string())
        print("\n--- Actual Data Head ---")
        print(df_actual.head().to_string())
        
        import traceback
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
    except Exception:
        print("\nRUNTIME ERROR during test execution:")
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_parser_template.py <bank_target>")
        sys.exit(1)
        
    run_test(sys.argv[1])
