FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY wt ./wt
COPY config ./config
COPY fixtures ./fixtures
COPY eval ./eval
RUN pip install --no-cache-dir .

ENTRYPOINT ["wt"]
CMD ["demo"]
