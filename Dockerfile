FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Single worker: the in-process scheduler must not be duplicated. Same command as Procfile.
CMD ["sh", "-c", "gunicorn webhook:app --workers 1 --threads 4 --bind 0.0.0.0:${PORT:-5000} --timeout 120"]