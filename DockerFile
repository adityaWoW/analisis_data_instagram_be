# Gunakan Python 3.10 slim agar image lebih kecil
FROM python:3.10-slim

# Set working directory di dalam container
WORKDIR /code

# Copy requirements dulu (supaya Docker bisa cache layer ini)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy semua kode ke dalam container
COPY ./app /code/app

# Expose port 7860 — ini port default yang dipakai Hugging Face Spaces
EXPOSE 7860

# Jalankan FastAPI dengan uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]