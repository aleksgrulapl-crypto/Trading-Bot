FROM python:3.10-slim

WORKDIR /app

# Force Docker to rebuild this layer by touching a dummy file
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy ALL source files AFTER dependencies
COPY . .

CMD ["python", "webhook.py"]
