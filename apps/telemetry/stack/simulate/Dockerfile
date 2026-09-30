FROM python:3.11.4 AS builder
COPY ./stack/simulate/requirements.txt .
RUN pip install -r requirements.txt

FROM builder AS executor
WORKDIR /app

COPY ./stack/simulate/ /app/stack/simulate/

# Protobuf schemas for building sensor_data frames.
COPY ./stack/ingest/protobuf/ /app/stack/ingest/protobuf/

ENV PYTHONPATH /app
WORKDIR /app/stack/simulate

CMD ["python", "./main.py"]
