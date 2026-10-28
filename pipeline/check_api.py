import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
models = {m.id for m in client.models.list()}
needed = {"muse-spark-1.3", "sam-3.1", "muse-image-1.0"}
print("OK" if needed <= models else f"足りない: {needed - models}")
