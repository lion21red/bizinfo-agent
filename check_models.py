# check_models.py
import os
import sys
from dotenv import load_dotenv
from google import genai

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(override=True)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

print("🔍 사용 가능한 모델 목록 조회 중...\n")
for m in client.models.list():
    if "generateContent" in m.supported_actions:
        print(f"- {m.name}")