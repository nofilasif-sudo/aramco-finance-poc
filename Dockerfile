FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py transformer.py helpers.py default_config.json ./
COPY sql/ ./sql/

CMD ["python", "main.py"]
