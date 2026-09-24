FROM python:3.11.4 AS builder
COPY ./stack/processors/events_faults/requirements.txt .
RUN pip install -r requirements.txt

FROM builder AS executor
WORKDIR /app

COPY ./stack/processors/events_faults/ /app/stack/processors/events_faults/

# Protobuf schemas for direct decode of sensor_data.
COPY ./stack/ingest/protobuf/ /app/stack/ingest/protobuf/

ENV PYTHONPATH /app
WORKDIR /app/stack/processors/events_faults

CMD ["python", "./main.py"]
