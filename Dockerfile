FROM python:3.11-alpine

WORKDIR /app

COPY src /app

RUN pip install -r requirements.txt

CMD ["python3", "app.py"]

