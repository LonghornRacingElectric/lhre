"""Database sessions for Orion and Artemis."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, URL
from sqlalchemy.orm import sessionmaker

if os.getenv("IN_DOCKER"):
    config_path = Path("/app/net_configs.json")
else:
    config_path = Path(__file__).parents[2] / "net_configs.json"
    from dotenv import load_dotenv
    load_dotenv()
with config_path.open() as source:
    global_target = json.load(source)

CARS = {"Orion": "orion", "Artemis": "artemis"}

class DBTarget:
    @staticmethod
    def get(client=False, car="Orion"):
        if car not in CARS:
            raise ValueError(f"Unsupported car: {car}")
        prefix = car.upper()
        host = global_target["TARGETS"][global_target["CLIENT_TARGET"]] if client else (
            "db" if os.getenv("IN_DOCKER") else global_target["TARGETS"][global_target["SERVER_TARGET"]])
        return {"dbname": CARS[car], "host": os.getenv(prefix + "_DB_HOST", host),
                "port": int(os.getenv(prefix + "_DB_PORT", "5432")),
                "users": {user: os.getenv(variable) for user, variable in
                          (("electric", "ELECTRIC_PWD"), ("grafana", "GRAFANA_PWD"), ("analysis", "ANALYSIS_PWD"))}}

def get_db_url(car="Orion", user="electric"):
    target = DBTarget.get(car=car)
    return os.getenv(car.upper() + "_DATABASE_URL") or URL.create(
        "postgresql+psycopg2", username=user, password=target["users"][user],
        host=target["host"], port=target["port"], database=target["dbname"])

engine_orion = create_engine(get_db_url("Orion"))
engine_artemis = create_engine(get_db_url("Artemis"))
SESSIONS = {car: sessionmaker(bind=engine, autoflush=False) for car, engine in
            (("Orion", engine_orion), ("Artemis", engine_artemis))}

@contextmanager
def get_db(car="Orion"):
    if car not in SESSIONS:
        raise ValueError(f"Unsupported car: {car}")
    with SESSIONS[car]() as session:
        yield session
