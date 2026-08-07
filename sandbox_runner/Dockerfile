FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir backtrader==1.9.78.123
COPY executor.py /opt/ciclotrade/executor.py
USER 65534:65534
ENTRYPOINT ["python", "/opt/ciclotrade/executor.py"]
