FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker ./worker
# The dispatcher reuses this image and overrides the command.
CMD ["python", "-m", "worker"]
