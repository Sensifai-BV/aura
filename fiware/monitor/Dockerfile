FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aura_monitor ./aura_monitor
ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "-m", "aura_monitor.app"]
