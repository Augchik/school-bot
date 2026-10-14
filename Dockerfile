FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir aiogram fastapi uvicorn pytz aiosqlite pydantic

COPY . .

CMD ["python", "main.py"]
