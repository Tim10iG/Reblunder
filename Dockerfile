FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends stockfish && rm -rf /var/lib/apt/lists/*
ENV STOCKFISH_PATH=/usr/games/stockfish PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY chesstrainer chesstrainer
VOLUME /app/data
EXPOSE 5000
CMD ["python", "-m", "chesstrainer", "serve", "--host", "0.0.0.0", "--auto-sync", "10"]
