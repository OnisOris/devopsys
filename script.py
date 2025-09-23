import sys

def generate_dockerfile() -> str:
    return """# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

ENV POETRY_VERSION=1.8.2 \
    PYTHONUNBUFFERED=1 \
    POETRY_HOME=/opt/poetry \
    PATH=$PATH:/opt/poetry/bin

RUN apt-get update && \\
    apt-get install -y --no-install-recommends curl build-essential git ca-certificates && \\
    curl -sSL https://install.python-poetry.org | python3 - --version $POETRY_VERSION && \\
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app/ ./

EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
"""

def main() -> None:
    dockerfile = generate_dockerfile()
    sys.stdout.write(dockerfile)

if __name__ == "__main__":
    main()
