FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/       core/
COPY web/        web/

# DATA_DIR holds aliases.json and config.json (mount a volume here)
ENV DATA_DIR=/data
ENV PORT=5000

EXPOSE 5000

CMD ["python", "-m", "web.app"]
