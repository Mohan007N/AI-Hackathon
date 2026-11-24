FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure data and logs directories exist
RUN mkdir -p data logs

# Expose web service port
EXPOSE 8000

# Set environment defaults for cloud execution
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV BROWSER_HEADLESS=true
ENV RENDER=true

# Start FastAPI uvicorn bound to Render dynamic $PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

