FROM python:3.10-slim

WORKDIR /app

# Install system dependencies (if any are needed later, e.g., for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# Copy all backend code
COPY . .

# Expose port 8000
EXPOSE 8000

# Start server with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]
