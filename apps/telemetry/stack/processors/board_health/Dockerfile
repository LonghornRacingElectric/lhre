FROM python:3.11.4 AS builder
COPY ./stack/processors/board_health/requirements.txt .
RUN pip install -r requirements.txt

FROM builder AS executor
WORKDIR /app

COPY ./stack/processors/board_health/ /app/stack/processors/board_health/

# Protobuf schemas for direct decode of sensor_data.
COPY ./stack/ingest/protobuf/ /app/stack/ingest/protobuf/

ENV PYTHONPATH /app
WORKDIR /app/stack/processors/board_health

CMD ["python", "./main.py"]
