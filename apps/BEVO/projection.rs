use prost::Message;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::Path;

include!(concat!(env!("OUT_DIR"), "/generated_projection.rs"));

pub struct Projection {
    accepted: BTreeMap<Vec<u32>, Vec<i32>>,
}

impl Projection {
    pub fn from_release(directory: &Path) -> Result<Self, String> {
        let metadata: Value = serde_json::from_slice(&std::fs::read(directory.join("pi_core_manifest.json")).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
        let plan: Value = serde_json::from_slice(&std::fs::read(directory.join("reconciliation.json")).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
        if plan["format_version"] != 1 || metadata["projection_version"] != 1
            || plan["producer_schema_id"] != PRODUCER_SCHEMA_ID || metadata["schema_id"] != PRODUCER_SCHEMA_ID
            || plan["metadata"] != metadata {
            return Err("reconciliation plan belongs to another executable release".to_string());
        }
        if plan["server"]["schema_id"].as_str().filter(|id| id.len() == 64).is_none() {
            return Err("missing deployed server schema identity".to_string());
        }
        let mut accepted = BTreeMap::new();
        for field in plan["accepted"].as_array().ok_or("missing numeric projection")? {
            let numbers = field["path"].as_array().ok_or("invalid numeric path")?.iter()
                .map(|number| number.as_u64().and_then(|value| u32::try_from(value).ok()).ok_or("invalid field number"))
                .collect::<Result<Vec<_>, _>>()?;
            let values = field["enum_values"].as_array().ok_or("missing enum intersection")?.iter()
                .map(|number| number.as_i64().and_then(|value| i32::try_from(value).ok()).ok_or("invalid enum number"))
                .collect::<Result<Vec<_>, _>>()?;
            if !NUMERIC_PATHS.iter().any(|known| *known == numbers.as_slice()) || accepted.insert(numbers, values).is_some() {
                return Err("unknown or duplicate projection path".to_string());
            }
        }
        if !accepted.contains_key(&vec![1]) || !accepted.contains_key(&vec![2]) || accepted.len() <= 2 {
            return Err("projection requires time, packet_id and shared telemetry".to_string());
        }
        Ok(Self { accepted })
    }

    pub fn project(&self, payload: &[u8]) -> Result<Vec<u8>, prost::DecodeError> {
        let source = crate::proto::orion::OrionSensorData::decode(payload)?;
        Ok(project_message(&source, &self.accepted).encode_to_vec())
    }
}
