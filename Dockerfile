FROM python:3.12-slim
# LibreOffice PowerPoint/Word fayllarining birinchi betini rasmga aylantirish uchun kerak
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress libreoffice-writer libreoffice-calc fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--timeout", "300", "app:app"]
