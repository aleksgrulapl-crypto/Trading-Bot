# force rebuild 2026-08-10
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# copy project files
COPY . .

CMD ["python", "webhook.py"]
