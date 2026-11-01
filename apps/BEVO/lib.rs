pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/_.rs"));
}

pub mod config;
pub mod projection;

pub mod generated_mapping {
    include!(concat!(env!("OUT_DIR"), "/generated_mapping.rs"));
}

pub fn set_vec_index<Value: Default + Clone>(values: &mut Vec<Value>, index: usize, value: Value) {
    if values.len() <= index { values.resize(index + 1, Value::default()); }
    values[index] = value;
}
