import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://vplzxrzoovtjvdksegbx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GREEN_TUNNEL_ENABLED = os.environ.get("GREEN_TUNNEL", "true").lower() == "true"
GREEN_TUNNEL_PORT = int(os.environ.get("GREEN_TUNNEL_PORT", "8000"))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

groq_client = Groq(api_key=GROQ_API_KEY)
