"""
AcmeCorp Data Pipeline — ingests sensor data from MQTT,
transforms via dbt, loads into Snowflake.
"""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = "kafka.internal.acmecorp.eu:9092"
TOPIC_RAW = "sensors.raw"
TOPIC_CLEAN = "sensors.cleaned"

def validate_reading(reading: dict) -> bool:
    required = {"timestamp", "sensor_id", "metric", "value"}
    if not required.issubset(reading.keys()):
        return False
    if not isinstance(reading["value"], (int, float)):
        return False
    if reading["value"] < -1000 or reading["value"] > 10000:
        logger.warning(f"Out of range value: {reading}")
        return False
    return True

def enrich_reading(reading: dict) -> dict:
    reading["ingested_at"] = datetime.utcnow().isoformat() + "Z"
    reading["pipeline_version"] = "1.4.2"
    facility_map = {
        "Lyon-Nord": {"region": "eu-west", "timezone": "Europe/Paris"},
        "Munich-Ost": {"region": "eu-central", "timezone": "Europe/Berlin"},
    }
    facility = reading.get("facility", "")
    if facility in facility_map:
        reading["facility_meta"] = facility_map[facility]
    return reading

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Pipeline ready")
