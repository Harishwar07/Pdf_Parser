import os
import sys
import argparse
import subprocess
import pandas as pd
import re
from pathlib import Path
from google import genai
from google.genai.errors import APIError

# --- Configuration & Paths ---
MAX_ATTEMPTS = 3
MODEL_NAME = "gemini-2.5-flash" 
PARSER_DIR = Path("custom_parsers")
TEST_SCRIPT = Path("tests/test_parser_template.py")
DATA_DIR = Path("data/icici")
PDF_PATH = DATA_DIR / "icici_sample.pdf"
CSV_PATH = DATA_DIR / "icici_expected.csv"
PARSER_PATH = PARSER_DIR / "icici_parser.py"

# The schema the agent must enforce (used in prompt generation)
CSV_SCHEMA_PROMPT = (
    "The target DataFrame columns are: ['Date', 'Description', 'Withdrawal', 'Deposit', 'Balance']. "
    "Input data uses 'Debit Amt' (maps to Withdrawal) and 'Credit Amt' (maps to Deposit). "
    "Withdrawal and Deposit columns must contain only positive floating-point numbers or NaN for empty/zero values. "
    "Dates must be standardized to YYYY-MM-DD format. You MUST use pdfplumber for table extraction."
)

# --- Utility Functions ---

def read_pdf_text_for_prompt(pdf_path: Path) -> str:
    """
    Reads the raw text from the PDF file using PyPDF2 (pypdf) to provide context to the LLM.
    This simulates the initial text extraction step an agent would perform.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        raise ImportError("PyPDF2 (pypdf) not installed. Cannot read PDF for context.")
        
    text = ""
    try:
        with open(pdf_path, "rb") as f:
            reader = PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        raise RuntimeError(f"Error reading PDF file {pdf_path}: {e}")
        
    # Simple sanitization before feeding to LLM
    cleaned = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return cleaned.strip()[:3000] # Truncate to limit token cost for the prompt


def get_llm_response(prompt: str) -> str:
    """Calls the Gemini API to get the generated code."""
    try:
        # Use os.getenv for API key access
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=MODEL_NAME, 
            contents=prompt,
            config={"temperature": 0.1} # Low temp for stable code
        )
        return response.text
    except APIError as e:
        return f"LLM_API_ERROR: {e}"
    except Exception as e:
        return f"LLM_API_ERROR: Client failure: {e}"

def extract_python_code(response: str) -> str:
    """Extracts the first markdown Python code block (```python ... ```)."""
    match = re.search(r"```python\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()

def save_parser(code: str, path: Path):
    """Saves the generated code to the parser file, ensuring essential imports are present."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # Ensure critical imports are present for the generated code (LLM might skip them)
    required_imports = [
        "import pandas as pd",
        "import pdfplumber",
        "import re"
    ]
    
    # Combine imports and generated code
    final_code = "\n".join(required_imports) + "\n\n" + code
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(final_code)

def run_test_and_capture_output(target: str) -> tuple[str, str]:
    """Runs the test script and captures the result and full output (T4)."""
    # Runs: python tests/test_parser_template.py <target>
    cmd = [sys.executable, str(TEST_SCRIPT), target]
    
    # Run the external test script (Tool Call)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )
    
    output = result.stdout + result.stderr
    
    # Test is successful if the return code is 0 AND the success marker is found
    if result.returncode == 0 and "SUCCESS" in output:
        return "PASS", output
    else:
        return "FAIL", output

def generate_prompt(target: str, pdf_text: str, csv_path: str, is_initial: bool, previous_code: str, error_trace: str) -> str:
    """Generates the planning/refinement prompt for the LLM."""
    
    # Read CSV to get the exact column names for the target schema
    try:
        df_expected = pd.read_csv(csv_path, encoding="utf-8")
        columns = df_expected.columns.tolist()
    except Exception:
        columns = ['Date', 'Description', 'Withdrawal', 'Deposit', 'Balance'] # Fallback

    base_prompt = f"""
You are an expert "Agent-as-Coder" AI. Your goal is to write a Python parser for the bank statement PDF.

CONTEXT:
- **Target Bank:** {target.upper()}
- **Target Schema (Columns):** {columns}.
- **PDF Raw Text Sample (for context):** --- START SAMPLE ---
{pdf_text}
--- END SAMPLE ---

CONSTRAINTS:
1.  **Tool Use:** You MUST use the `pdfplumber` library to handle tables robustly.
2.  **Parser Contract (T3):** The file MUST define a single function: `def parse(pdf_path: str) -> pd.DataFrame:`.
3.  **Data Transformation:** Map the input columns ('Debit Amt' and 'Credit Amt') to the target schema ('Withdrawal' and 'Deposit'). Missing values must be NaN. Dates must be standardized to YYYY-MM-DD format.
"""
    if is_initial:
        return base_prompt + "\n**TASK:** Write the complete initial Python code for the function. Output ONLY the Python code block (using ```python ... ```)."
    else:
        # Refinement Prompt (T1: Self-Fix)
        return f"""{base_prompt}

Your previous code attempt failed the automated tests. Analyze the **ERROR TRACE** and **PREVIOUS CODE** to identify and fix the bug.

PREVIOUS CODE (FAILED):
```python
{previous_code}
```

ERROR TRACE (OBSERVATION):
```
{error_trace}
```

**INSTRUCTIONS:** Write the complete, fixed Python code block containing ONLY the `parse` function and necessary helpers. Do not include commentary.
"""

# --- Main Agent Logic (T1, T2) ---

def run_agent(target: str):
    """Orchestrates the agent's plan-code-test-refine loop."""
    print(f"--- Agent Initialized for Target: {target.upper()} ---")
    
    if not all([PDF_PATH.exists(), CSV_PATH.exists(), TEST_SCRIPT.exists()]):
        # Removed the sys.exit(1) here for the immediate user experience
        print(f"Error: Missing required files for target '{target}'. Ensure files exist at:")
        print(f"- {PDF_PATH}")
        print(f"- {CSV_PATH}")
        print(f"- {TEST_SCRIPT}")
        print("Please create the files and try again.")
        sys.exit(1)

    # 1. Get raw text from the PDF for the LLM prompt
    try:
        pdf_text = read_pdf_text_for_prompt(PDF_PATH)
    except Exception as e:
        print(f"Critical Error: Failed to extract text from PDF: {e}")
        sys.exit(1)
    
    # State Initialization
    attempts = 0
    previous_code = ""
    error_trace = ""
    
    # The Core Agent Loop (T1)
    while attempts < MAX_ATTEMPTS:
        print(f"\n{'='*20} [Attempt {attempts + 1}/{MAX_ATTEMPTS}] {'='*20}")

        # A. Plan / Generate Code
        is_initial = (attempts == 0)
        # Pass the PDF text and path strings to the prompt function
        prompt = generate_prompt(target, pdf_text, str(CSV_PATH), is_initial, previous_code, error_trace)
        print(f"   ({ 'Planning & Initial Code Generation' if is_initial else 'Self-Fix & Code Refinement' }...)")

        llm_output = get_llm_response(prompt)
        
        if "LLM_API_ERROR" in llm_output:
            print(f"   LLM failed to respond: {llm_output}. Aborting.")
            break
            
        generated_code = extract_python_code(llm_output)
        
        if not generated_code.strip():
             print("[AGENT] Error: LLM returned empty or unparseable code block. Aborting.")
             break

        # B. Write Code (Tool: File I/O)
        save_parser(generated_code, PARSER_PATH)
        print(f"   Code written to {PARSER_PATH}")

        # C. Run Tests (Tool: Subprocess/T4)
        print("   (Running Tests...)")
        result, output = run_test_and_capture_output(target)

        # D. Observe Results & Decide (Self-Correction)
        if result == "PASS":
            print("\n" + "#" * 60)
            print(f"AGENT SUCCESS: Parser `{PARSER_PATH}` passed all tests (T4).")
            print("#" * 60)
            return
        
        # Test Failed: Prepare for Refinement
        print(f"   Test failed. Preparing for refinement (T1).")
        error_trace = output
        previous_code = generated_code
        attempts += 1

    # Final Outcome
    print("\n" + "X" * 60)
    print(f"AGENT FAILURE: Failed to generate a correct parser after {MAX_ATTEMPTS} attempts.")
    print("X" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent-as-Coder Challenge.")
    parser.add_argument("--target", required=True, help="Bank target (e.g., icici).")
    args = parser.parse_args()
    
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY environment variable not set. Please set it before running.")
        sys.exit(1)

    run_agent(args.target.lower())
