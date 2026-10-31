FROM python:3.12-slim
WORKDIR /app
COPY app.py .
COPY static ./static
ENV DATA_DIR=/app/data PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "app.py"]
