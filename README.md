# Portal Backend

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Provide the required environment variables (see `app/core.py` for defaults) or create a `.env` file.
4. Run the application:
   ```bash
   python main.py
   ```

## Deployment Notes

Ensure the target environment installs the dependencies from `requirements.txt` before starting `main.py` or deploying the WSGI app entrypoint `main:app`.
