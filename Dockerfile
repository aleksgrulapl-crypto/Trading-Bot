FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# cache breaker
COPY cachebreaker.txt /app/cachebreaker.txt

# copy project files
COPY . .

CMD ["python", "webhook.py"]