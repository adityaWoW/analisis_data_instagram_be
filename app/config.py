from dotenv import load_dotenv
from pathlib import Path
import os

env_path = Path('.') / '.env'

load_dotenv(dotenv_path=env_path)

HOST = os.getenv(
    "HOST"
)

PORT = int(
    os.getenv("PORT")
)
