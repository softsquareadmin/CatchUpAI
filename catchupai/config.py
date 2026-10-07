import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MODEL = "gpt-realtime-2.1"
OPENAI_SAMPLE_RATE = 24000
REALTIME_URL = (
    f"wss://api.openai.com/v1/realtime"
    f"?model={MODEL}"
)
CHUNK_MS = 200
ANALYSIS_INTERVAL = 30

