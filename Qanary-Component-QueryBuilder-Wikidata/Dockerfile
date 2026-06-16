FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY component/ ./component/
COPY run.py ./

# shell form so $SERVER_PORT is expanded at container start
ENTRYPOINT uvicorn run:app --host 0.0.0.0 --port $SERVER_PORT
# for additional options see uvicorn documentation
