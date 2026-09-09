FROM python:3.12-slim

WORKDIR /app

# نسخ ملف المتطلبات أولاً للاستفادة من التخزين المؤقت
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الملفات
COPY . .

# تشغيل البوت
CMD ["python", "casinobot.py"]
