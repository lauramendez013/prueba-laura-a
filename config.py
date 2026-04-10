# app/config.py
import os
from langchain_google_genai import ChatGoogleGenerativeAI

def get_llm():
    api_key = os.getenv("GOOGLE_API_KEY")  
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env")
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
