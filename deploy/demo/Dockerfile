FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 10001 --create-home app && mkdir /state && chown app /state
COPY src/demo /app/src/demo
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["uvicorn", "src.demo.api:from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
