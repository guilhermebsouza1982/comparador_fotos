## 2025-05-18 - Unsafe Command String Invocation in Windows Subprocess
**Vulnerability:** Constructing raw string commands with user-influenced file paths for `subprocess.Popen(f'explorer /select,"{p}"')` allows argument injection or execution errors if paths contain special characters or quotes.
**Learning:** `subprocess.Popen` in Python on Windows parses raw strings by passing them directly to `CreateProcessW` without shell argument separation.
**Prevention:** Always pass subprocess arguments as a list (e.g. `subprocess.Popen(["explorer", "/select,", str(p)])`) or use standard platform APIs like `os.startfile`.
